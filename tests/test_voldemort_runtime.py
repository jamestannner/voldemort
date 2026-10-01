import ast
import builtins
import hashlib
import json
import os
import subprocess
import sys
import threading

import pytest

from voldemort import Voldemort, runtime
from voldemort.runtime import load_evidence, record
from vulture.config import InputError

from . import REPO


def write_source(root, source, name="example.py"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def write_evidence(root, source, observations, name="calls.json"):
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    entries = [
        {
            "path": source.relative_to(root).as_posix(),
            "lineno": lineno,
            "qualname": qualified_name,
            "source_hash": digest,
        }
        for lineno, qualified_name in observations
    ]
    path = root / name
    path.write_text(
        json.dumps({"version": 1, "observations": entries}), encoding="utf-8"
    )
    return path


def invoke_recorder(root, *, module=None, script=None, args=(), cwd=None):
    output = root / "calls.json"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(REPO), environment.get("PYTHONPATH", "")]
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "voldemort.runtime",
            "--output",
            str(output),
            "--project-root",
            str(root),
            *(("--module", module) if module else ("--script", str(script))),
            "--",
            *args,
        ],
        cwd=cwd or root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, output


def observations(path):
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["version"] == 1
    return document["observations"]


def test_observed_definitions_are_exact_and_preserve_other_candidates(
    tmp_path,
):
    source = write_source(
        tmp_path,
        "def dynamic(dynamic, spare):\n"
        "    dead_local = 1\n"
        "\n"
        "class First:\n"
        "    def dynamic(self):\n"
        "        pass\n"
        "class Second:\n"
        "    def dynamic(self):\n"
        "        pass\n"
        "def unobserved():\n"
        "    pass\n",
    )
    trace = write_evidence(
        tmp_path, source, [(1, "dynamic"), (5, "First.dynamic")]
    )
    scanner = Voldemort(
        project_root=tmp_path, django_enabled=False, runtime_evidence=[trace]
    )
    scanner.scan(source.read_text(encoding="utf-8"), filename=source)

    assert [item.name for item in scanner.unused_funcs] == ["unobserved"]
    assert [
        (item.name, item.first_lineno) for item in scanner.unused_methods
    ] == [("dynamic", 8)]
    assert {item.name for item in scanner.unused_vars} == {
        "dynamic",
        "spare",
        "dead_local",
    }
    assert {item.name for item in scanner.unused_classes} == {
        "First",
        "Second",
    }
    assert {entry.qualified_name for entry in scanner.runtime_evidence} == {
        "example.dynamic",
        "example.First.dynamic",
    }
    assert all(
        entry.rule == "runtime.observed-call"
        for entry in scanner.runtime_evidence
    )


def test_nested_decorated_async_and_property_match_real_qualnames(tmp_path):
    source = write_source(
        tmp_path,
        "def outer():\n"
        "    @decorator\n"
        "    async def inner(spare):\n"
        "        pass\n"
        "    class Inside:\n"
        "        @property\n"
        "        def value(self):\n"
        "            pass\n",
        name="pkg/__init__.py",
    )
    trace = write_evidence(
        tmp_path,
        source,
        [(2, "outer.<locals>.inner"), (6, "outer.<locals>.Inside.value")],
    )
    tree = ast.parse(source.read_text(encoding="utf-8"))
    analysis = load_evidence([trace], {source: tree}, tmp_path)

    assert {entry.qualified_name for entry in analysis.evidence.values()} == {
        "pkg.outer.<locals>.inner",
        "pkg.outer.<locals>.Inside.value",
    }
    assert not analysis.notes
    nodes = {id(node) for node in ast.walk(tree)}
    assert all(entry.node_id in nodes for entry in analysis.evidence.values())


def test_stale_unknown_and_outside_observations_are_notes(tmp_path):
    source = write_source(tmp_path, "def helper():\n    pass\n")
    trace = write_evidence(tmp_path, source, [(1, "helper"), (1, "Unknown")])
    document = json.loads(trace.read_text())
    document["observations"].append(
        {**document["observations"][0], "path": "../escape.py"}
    )
    document["observations"].append(
        {**document["observations"][0], "path": "missing.py"}
    )
    document["observations"][0]["source_hash"] = "0" * 64
    trace.write_text(json.dumps(document))

    analysis = load_evidence(
        [trace], {source: ast.parse(source.read_bytes())}, tmp_path
    )
    assert not analysis.evidence
    assert any("stale" in note for note in analysis.notes)
    assert any("does not match" in note for note in analysis.notes)
    assert any("outside project root" in note for note in analysis.notes)
    assert any("not uniquely scanned" in note for note in analysis.notes)


def test_symlink_outside_project_does_not_match(tmp_path):
    outside = write_source(
        tmp_path.parent, "def helper():\n    pass\n", "outside.py"
    )
    link = tmp_path / "link.py"
    link.symlink_to(outside)
    trace = write_evidence(tmp_path, link, [(1, "helper")])
    analysis = load_evidence(
        [trace], {link: ast.parse(link.read_bytes())}, tmp_path
    )
    assert not analysis.evidence
    assert any("outside project root" in note for note in analysis.notes)


@pytest.mark.parametrize(
    "document",
    [
        None,
        [],
        {"version": True, "observations": []},
        {"version": 2, "observations": []},
        {"version": 1, "observations": {}},
        {"version": 1, "observations": [{}]},
        {"version": 1, "observations": [], "extra": True},
    ],
)
def test_invalid_schema_is_an_input_error(tmp_path, document):
    path = tmp_path / "calls.json"
    path.write_text(json.dumps(document))
    with pytest.raises(InputError, match=r"schema|observation"):
        load_evidence([path], {}, tmp_path)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("path", ""),
        ("path", None),
        ("qualname", 3),
        ("qualname", ""),
        ("lineno", True),
        ("lineno", 0),
        ("lineno", "1"),
        ("source_hash", "XYZ"),
        ("source_hash", 0),
    ],
)
def test_invalid_observation_is_an_input_error(tmp_path, key, value):
    source = write_source(tmp_path, "def helper():\n    pass\n")
    trace = write_evidence(tmp_path, source, [(1, "helper")])
    document = json.loads(trace.read_text())
    document["observations"][0][key] = value
    trace.write_text(json.dumps(document))
    with pytest.raises(InputError, match="observation"):
        load_evidence([trace], {}, tmp_path)


@pytest.mark.parametrize(
    "contents", [b"{", b"\xff", b'{"version":1,"version":1}']
)
def test_unreadable_json_is_an_input_error(tmp_path, contents):
    path = tmp_path / "calls.json"
    path.write_bytes(contents)
    with pytest.raises(InputError, match="Cannot read runtime evidence"):
        load_evidence([path], {}, tmp_path)


def test_missing_evidence_file_is_an_input_error(tmp_path):
    with pytest.raises(InputError, match="Cannot read runtime evidence"):
        load_evidence([tmp_path / "missing.json"], {}, tmp_path)


def test_scanned_text_must_match_disk_even_when_ast_is_identical(tmp_path):
    source = write_source(tmp_path, "# disk\ndef helper():\n    pass\n")
    trace = write_evidence(tmp_path, source, [(2, "helper")])
    scanner = Voldemort(
        project_root=tmp_path, django_enabled=False, runtime_evidence=[trace]
    )
    scanner.scan("# scan\ndef helper():\n    pass\n", filename=source)
    assert [item.name for item in scanner.unused_funcs] == ["helper"]
    assert any(
        "differs from scanned source" in note for note in scanner.runtime_notes
    )


def test_ast_mismatch_is_rejected_without_source_texts(tmp_path):
    source = write_source(tmp_path, "def helper():\n    return 1\n")
    trace = write_evidence(tmp_path, source, [(1, "helper")])
    analysis = load_evidence(
        [trace], {source: ast.parse("def helper():\n    return 2\n")}, tmp_path
    )
    assert not analysis.evidence
    assert any(
        "differs from scanned source" in note for note in analysis.notes
    )


def test_evidence_options_and_file_changes_invalidate_analysis(tmp_path):
    source = write_source(
        tmp_path, "def first():\n    pass\ndef second():\n    pass\n"
    )
    first = write_evidence(tmp_path, source, [(1, "first")], "first.json")
    second = write_evidence(tmp_path, source, [(3, "second")], "second.json")
    scanner = Voldemort(
        project_root=tmp_path, django_enabled=False, runtime_evidence=[first]
    )
    scanner.scan(source.read_text(), filename=source)
    assert [item.name for item in scanner.unused_funcs] == ["second"]
    scanner.runtime_evidence_paths[:] = [second]
    assert [item.name for item in scanner.unused_funcs] == ["first"]
    write_evidence(tmp_path, source, [], "second.json")
    assert {item.name for item in scanner.unused_funcs} == {"first", "second"}


def test_encoded_source_and_universal_newlines(tmp_path):
    source = tmp_path / "encoded.py"
    source.write_bytes(
        b"# coding: latin-1\r\n# caf\xe9\r\ndef helper():\r\n    pass\r\n"
    )
    trace = write_evidence(tmp_path, source, [(3, "helper")])
    scanner = Voldemort(
        project_root=tmp_path, django_enabled=False, runtime_evidence=[trace]
    )
    scanner.scan(source.read_bytes().decode("latin-1"), filename=source)
    assert not scanner.unused_funcs


def test_recorder_runs_script_with_argv_and_local_imports(tmp_path):
    script = write_source(
        tmp_path,
        "import json, sys\n"
        "from sibling import called\n"
        "class NeverInstantiated:\n"
        "    pass\n"
        "called()\n"
        "print(json.dumps([sys.argv, sys.path[0], __name__]))\n",
        "scripts/runner.py",
    )
    sibling = write_source(
        tmp_path,
        "def called():\n    hidden_argument = 'SECRET'\n",
        "scripts/sibling.py",
    )
    result, trace = invoke_recorder(
        tmp_path, script=script, args=("--flag", "value")
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        [str(script), "--flag", "value"],
        str(script.parent),
        "__main__",
    ]
    assert observations(trace) == [
        {
            "path": "scripts/sibling.py",
            "lineno": 1,
            "qualname": "called",
            "source_hash": hashlib.sha256(sibling.read_bytes()).hexdigest(),
        }
    ]
    assert "SECRET" not in trace.read_text()


def test_recorder_runs_module_and_captures_package_initialization(tmp_path):
    package = write_source(
        tmp_path,
        "def initialized():\n    pass\ninitialized()\n",
        "pkg/__init__.py",
    )
    main = write_source(
        tmp_path,
        "import json, sys\n"
        "def called():\n    pass\ncalled()\n"
        "print(json.dumps([sys.argv, __name__, __package__]))\n",
        "pkg/__main__.py",
    )
    result, trace = invoke_recorder(tmp_path, module="pkg", args=("extra",))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        [str(main), "extra"],
        "__main__",
        "pkg",
    ]
    assert {
        (entry["path"], entry["qualname"]) for entry in observations(trace)
    } == {("pkg/__init__.py", "initialized"), ("pkg/__main__.py", "called")}
    analysis = load_evidence(
        [trace],
        {path: ast.parse(path.read_bytes()) for path in (package, main)},
        tmp_path,
    )
    assert len(analysis.evidence) == 2
    assert not analysis.notes


@pytest.mark.parametrize(
    ("ending", "status"),
    [("raise RuntimeError('failure')", 1), ("raise SystemExit(7)", 7)],
)
def test_recorder_saves_calls_when_target_fails(tmp_path, ending, status):
    script = write_source(
        tmp_path, "def called():\n    pass\ncalled()\n" + ending + "\n"
    )
    result, trace = invoke_recorder(tmp_path, script=script)
    assert result.returncode == status
    assert [entry["qualname"] for entry in observations(trace)] == ["called"]
    if status == 1:
        assert "RuntimeError: failure" in result.stderr


def test_recorder_captures_future_threads(tmp_path):
    script = write_source(
        tmp_path,
        "import threading\n"
        "def called():\n    pass\n"
        "worker = threading.Thread(target=called)\n"
        "worker.start()\nworker.join()\n",
    )
    result, trace = invoke_recorder(tmp_path, script=script)
    assert result.returncode == 0, result.stderr
    assert [entry["qualname"] for entry in observations(trace)] == ["called"]


def test_recorder_restores_profiles_and_interpreter_state_after_exit(tmp_path):
    script = write_source(
        tmp_path,
        "import sys\nsys.argv = ['changed']\n"
        "sys.path = []\nraise SystemExit(4)\n",
    )
    previous_profile = sys.getprofile()
    previous_threads = threading.getprofile()
    previous_argv = sys.argv
    previous_path = sys.path
    path_values = sys.path[:]

    def profile(_frame, _event, _argument):
        pass

    sys.setprofile(profile)
    threading.setprofile(profile)
    try:
        with pytest.raises(SystemExit) as error:
            record(tmp_path / "calls.json", tmp_path, script=script)
        assert error.value.code == 4
        assert sys.getprofile() is profile
        assert threading.getprofile() is profile
        assert sys.argv is previous_argv
        assert sys.path is previous_path
        assert sys.path == path_values
        assert observations(tmp_path / "calls.json") == []
    finally:
        sys.setprofile(previous_profile)
        threading.setprofile(previous_threads)


def test_outliving_worker_restores_inherited_profile(tmp_path, monkeypatch):
    state = {
        "started": threading.Event(),
        "resume": threading.Event(),
        "profiles": [],
    }
    monkeypatch.setattr(
        builtins, "_voldemort_runtime_thread", state, raising=False
    )
    script = write_source(
        tmp_path,
        "import builtins, sys, threading\n"
        "state = builtins._voldemort_runtime_thread\n"
        "def worker():\n"
        "    state['started'].set()\n"
        "    state['resume'].wait()\n"
        "    state['profiles'].append(sys.getprofile())\n"
        "state['thread'] = threading.Thread(target=worker, daemon=True)\n"
        "state['thread'].start()\n"
        "state['started'].wait()\n",
    )
    previous_threads = threading.getprofile()

    def inherited_profile(_frame, _event, _argument):
        pass

    threading.setprofile(inherited_profile)
    try:
        record(tmp_path / "calls.json", tmp_path, script=script)
        assert threading.getprofile() is inherited_profile
        state["resume"].set()
        state["thread"].join(timeout=2)
        assert not state["thread"].is_alive()
        assert state["profiles"] == [inherited_profile]
        assert [
            entry["qualname"]
            for entry in observations(tmp_path / "calls.json")
        ] == ["worker"]
    finally:
        state["resume"].set()
        state["thread"].join(timeout=2)
        threading.setprofile(previous_threads)


def test_recorder_rejects_loaded_code_that_differs_from_first_event_source(
    tmp_path,
):
    module = write_source(
        tmp_path, "def called():\n    return 1\n", "stale.py"
    )
    script = write_source(
        tmp_path,
        "from pathlib import Path\nimport stale\n"
        "Path(stale.__file__).write_text('def called():\\n    return 2\\n')\n"
        "stale.called()\n",
        "runner.py",
    )
    result, trace = invoke_recorder(tmp_path, script=script)
    assert result.returncode == 0, result.stderr
    assert not observations(trace)
    assert module.read_text() == "def called():\n    return 2\n"


def test_recorder_hash_is_from_first_call_and_later_source_is_stale(tmp_path):
    module = write_source(
        tmp_path, "def called():\n    return 1\n", "stale.py"
    )
    digest = hashlib.sha256(module.read_bytes()).hexdigest()
    script = write_source(
        tmp_path,
        "from pathlib import Path\nimport stale\nstale.called()\n"
        "Path(stale.__file__).write_text('def called():\\n    return 2\\n')\n"
        "stale.called()\n",
        "runner.py",
    )
    result, trace = invoke_recorder(tmp_path, script=script)
    assert result.returncode == 0, result.stderr
    assert observations(trace)[0]["source_hash"] == digest
    analysis = load_evidence(
        [trace], {module: ast.parse(module.read_bytes())}, tmp_path
    )
    assert not analysis.evidence
    assert any("stale" in note for note in analysis.notes)


def test_recorder_invalid_options_return_two_without_target_execution(
    tmp_path,
):
    result, trace = invoke_recorder(tmp_path, script=tmp_path / "missing.py")
    assert result.returncode == 2
    assert "does not exist" in result.stderr
    assert not trace.exists()


def test_deep_source_is_a_note_and_never_breaks_workload(tmp_path):
    source = write_source(
        tmp_path,
        "def helper():\n    return "
        + " + ".join(["1"] * 1600)
        + "\nhelper()\n",
    )
    trace = write_evidence(tmp_path, source, [(1, "helper")])
    analysis = load_evidence(
        [trace], {source: ast.parse(source.read_bytes())}, tmp_path
    )
    assert not analysis.evidence
    assert any(
        "Cannot validate runtime source" in note for note in analysis.notes
    )
    record(trace, tmp_path, script=source)
    assert not observations(trace)


@pytest.mark.parametrize(
    ("limit", "value"),
    [
        ("_MAX_OBSERVATIONS", 1),
        ("_MAX_FILE_BYTES", 400),
        ("_MAX_LOCATION_LENGTH", 5),
    ],
)
def test_recorder_limits_produce_loadable_positive_subset(
    tmp_path, monkeypatch, limit, value
):
    source = write_source(
        tmp_path,
        "def first():\n    pass\ndef second():\n    pass\nfirst()\nsecond()\n",
    )
    monkeypatch.setattr(runtime, limit, value)
    trace = tmp_path / "calls.json"
    record(trace, tmp_path, script=source)
    analysis = load_evidence(
        [trace], {source: ast.parse(source.read_bytes())}, tmp_path
    )
    assert len(analysis.evidence) <= 1
    assert not analysis.notes


def test_write_error_does_not_override_target_exit(
    tmp_path, monkeypatch, capsys
):
    script = write_source(tmp_path, "raise SystemExit(7)\n")

    def replace(_source, _destination):
        raise OSError("output unavailable")

    monkeypatch.setattr(runtime.os, "replace", replace)
    with pytest.raises(SystemExit) as error:
        record(tmp_path / "calls.json", tmp_path, script=script)
    assert error.value.code == 7
    assert "Cannot write runtime evidence" in capsys.readouterr().err


def test_type_comments_and_type_ignores_match_scanned_tree(tmp_path):
    source = write_source(
        tmp_path,
        "def helper():  # type: () -> None\n    spare = 1  # type: ignore\n",
    )
    trace = write_evidence(tmp_path, source, [(1, "helper")])
    scanner = Voldemort(
        project_root=tmp_path, django_enabled=False, runtime_evidence=[trace]
    )
    scanner.scan(source.read_text(), filename=source)
    assert not scanner.unused_funcs
    assert not scanner.runtime_notes


def test_disk_changes_invalidate_accepted_evidence_without_rescan(tmp_path):
    source = write_source(tmp_path, "def helper():\n    return 1\n")
    trace = write_evidence(tmp_path, source, [(1, "helper")])
    scanner = Voldemort(
        project_root=tmp_path, django_enabled=False, runtime_evidence=[trace]
    )
    scanner.scan(source.read_text(), filename=source)
    assert not scanner.unused_funcs
    source.write_text("def helper():\n    return 2\n")
    assert [item.name for item in scanner.unused_funcs] == ["helper"]
    assert any("stale" in note for note in scanner.runtime_notes)


def test_unresolvable_symlink_is_a_coverage_note(tmp_path):
    source = write_source(tmp_path, "def helper():\n    pass\n")
    trace = write_evidence(tmp_path, source, [(1, "helper")])
    document = json.loads(trace.read_text())
    document["observations"][0]["path"] = "cycle.py"
    trace.write_text(json.dumps(document))
    (tmp_path / "cycle.py").symlink_to("cycle.py")
    analysis = load_evidence([trace], {}, tmp_path)
    assert not analysis.evidence
    assert any("Cannot resolve" in note for note in analysis.notes)
