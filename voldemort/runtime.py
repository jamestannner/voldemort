"""Record explicit runtime calls and validate their positive evidence.

Scanning never invokes the recorder. Run it explicitly around a test suite or
another workload, then supply its JSON file to Voldemort. Observations contain
source locations and hashes, never argument values, locals, or call counts.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
import re
import runpy
import sys
import tempfile
import threading
import tokenize
from contextlib import suppress
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import CodeType

from vulture.config import InputError
from vulture.lines import get_first_line_number

from .django import Analysis, Evidence

_SCHEMA_VERSION = 1
_MAX_FILE_BYTES = 32 * 1024 * 1024
_MAX_OBSERVATIONS = 100_000
_MAX_LOCATION_LENGTH = 4096
_CALL_TYPES = frozenset({"function", "method", "property"})


class _RecorderError(InputError):
    pass


def _definitions(tree):
    """Yield function definitions using Python's lexical qualified names."""
    definitions = []

    class Visitor(ast.NodeVisitor):
        prefix = ""

        def visit_FunctionDef(self, node):
            qualified_name = self.prefix + node.name
            definitions.append((qualified_name, node))
            previous = self.prefix
            self.prefix = qualified_name + ".<locals>."
            self.generic_visit(node)
            self.prefix = previous

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node):
            previous = self.prefix
            self.prefix += node.name + "."
            self.generic_visit(node)
            self.prefix = previous

    Visitor().visit(tree)
    return definitions


def _normalized_text(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _decode_source(raw):
    encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
    return _normalized_text(raw.decode(encoding))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _read_document(filename):
    try:
        with Path(filename).open("rb") as stream:
            raw = stream.read(_MAX_FILE_BYTES + 1)
        if len(raw) > _MAX_FILE_BYTES:
            raise ValueError("file exceeds the 32 MiB limit")
        document = json.loads(raw, object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        raise InputError(
            f"Cannot read runtime evidence {filename}: {error}"
        ) from error
    if (
        type(document) is not dict
        or set(document) != {"version", "observations"}
        or type(document["version"]) is not int
        or document["version"] != _SCHEMA_VERSION
        or type(document["observations"]) is not list
        or len(document["observations"]) > _MAX_OBSERVATIONS
    ):
        raise InputError(f"Invalid runtime evidence schema in {filename}")
    for position, observation in enumerate(document["observations"], 1):
        if (
            type(observation) is not dict
            or set(observation)
            != {"path", "lineno", "qualname", "source_hash"}
            or type(observation["lineno"]) is not int
            or observation["lineno"] < 1
            or any(
                not isinstance(observation[key], str)
                or not observation[key]
                or len(observation[key]) > _MAX_LOCATION_LENGTH
                or "\0" in observation[key]
                for key in ("path", "qualname")
            )
            or not isinstance(observation["source_hash"], str)
            or re.fullmatch(r"[0-9a-f]{64}", observation["source_hash"])
            is None
        ):
            raise InputError(
                f"Invalid runtime observation {position} in {filename}"
            )
    return document["observations"]


def load_evidence(paths, modules, project_root, *, source_texts=None):
    """Retain only observed functions from the exact scanned source.

    ``modules`` maps scanned filenames to their ASTs. ``source_texts`` may
    additionally provide the scanned strings; when present, they must match
    the current file after universal newline decoding. Unscanned, changed,
    or unresolvable observations are coverage notes, never exemptions.
    """
    result = Analysis()
    root = Path(project_root).resolve()
    scanned = {}
    for filename, tree in modules.items():
        scanned.setdefault(Path(filename).resolve(), []).append(
            (Path(filename), tree)
        )
    supplied_texts = (
        {Path(name).resolve(): text for name, text in source_texts.items()}
        if source_texts is not None
        else None
    )
    sources = {}
    notes = set()

    def note(message):
        if message not in notes:
            result.notes.append(message)
            notes.add(message)

    for evidence_file in paths:
        for observation in _read_document(evidence_file):
            location = observation["path"]
            relative = PurePosixPath(location)
            if (
                relative.is_absolute()
                or PureWindowsPath(location).is_absolute()
                or "\\" in location
                or relative.suffix != ".py"
            ):
                note(
                    "Ignored runtime observation with invalid path: "
                    f"{location}"
                )
                continue
            try:
                filename = (root / relative).resolve()
                filename.relative_to(root)
            except ValueError:
                note(
                    "Ignored runtime observation outside project root: "
                    f"{location}"
                )
                continue
            except (OSError, RuntimeError) as error:
                note(f"Cannot resolve runtime source {location}: {error}")
                continue
            entries = scanned.get(filename, [])
            if len(entries) != 1:
                note(f"Runtime source is not uniquely scanned: {location}")
                continue
            original_filename, tree = entries[0]
            if filename not in sources:
                try:
                    raw = filename.read_bytes()
                    disk_tree = ast.parse(
                        raw, filename=str(filename), type_comments=True
                    )
                    text_matches = supplied_texts is None or (
                        filename in supplied_texts
                        and _normalized_text(supplied_texts[filename])
                        == _decode_source(raw)
                    )
                    ast_matches = ast.dump(
                        disk_tree, include_attributes=True
                    ) == ast.dump(tree, include_attributes=True)
                    sources[filename] = (
                        hashlib.sha256(raw).hexdigest(),
                        text_matches and ast_matches,
                        {
                            (get_first_line_number(node), qualified_name): node
                            for qualified_name, node in _definitions(tree)
                        },
                    )
                except (
                    OSError,
                    SyntaxError,
                    UnicodeError,
                    ValueError,
                    RecursionError,
                ) as error:
                    sources[filename] = None
                    note(f"Cannot validate runtime source {location}: {error}")
            source = sources[filename]
            if source is None:
                continue
            digest, matches_scan, definitions = source
            if digest != observation["source_hash"]:
                note(f"Ignored stale runtime evidence: {location}")
                continue
            if not matches_scan:
                note(f"Runtime source differs from scanned source: {location}")
                continue
            node = definitions.get(
                (observation["lineno"], observation["qualname"])
            )
            if node is None:
                note(
                    f"Runtime call does not match a scanned function: "
                    f"{location}:{observation['lineno']} "
                    f"{observation['qualname']}"
                )
                continue
            parts = list(relative.with_suffix("").parts)
            if parts[-1:] == ["__init__"]:
                parts.pop()
            qualified_name = ".".join([*parts, observation["qualname"]])
            entry = Evidence(
                original_filename,
                get_first_line_number(node),
                node.name,
                qualified_name,
                "runtime.observed-call",
                "Called during the explicitly recorded workload.",
                _CALL_TYPES,
                id(node),
            )
            result.evidence[(entry.filename, entry.lineno, entry.name)] = entry
    return result


def _code_identity(code):
    """Compare executed code with source, including nested constants."""
    constants = tuple(
        _code_identity(value)
        if isinstance(value, CodeType)
        else (type(value), value)
        for value in code.co_consts
    )
    line_table = (
        code.co_linetable
        if hasattr(code, "co_linetable")
        else getattr(code, "co_lnotab", None)
    )
    return (
        code.co_name,
        getattr(code, "co_qualname", None),
        code.co_firstlineno,
        code.co_argcount,
        code.co_posonlyargcount,
        code.co_kwonlyargcount,
        code.co_nlocals,
        code.co_flags,
        code.co_code,
        constants,
        code.co_names,
        code.co_varnames,
        code.co_freevars,
        code.co_cellvars,
        line_table,
        getattr(code, "co_exceptiontable", None),
    )


class _Recorder:
    def __init__(self, root, previous_thread_profile):
        self.root = root
        self.previous_thread_profile = previous_thread_profile
        self.sources = {}
        self.observations = {}
        self.lock = threading.Lock()
        self.active = True
        self.output_bytes = 64

    def _source(self, filename):
        try:
            raw = filename.read_bytes()
            tree = ast.parse(raw, filename=str(filename))
            names = {
                (get_first_line_number(node), node.name): qualified_name
                for qualified_name, node in _definitions(tree)
            }
            compiled = compile(
                raw,
                str(filename),
                "exec",
                dont_inherit=True,
                optimize=sys.flags.optimize,
            )
            identities = {}

            def visit(code):
                qualified_name = names.get((code.co_firstlineno, code.co_name))
                if qualified_name is not None:
                    identities[(code.co_firstlineno, qualified_name)] = (
                        _code_identity(code)
                    )
                for value in code.co_consts:
                    if isinstance(value, CodeType):
                        visit(value)

            visit(compiled)
            return hashlib.sha256(raw).hexdigest(), names, identities
        except (
            OSError,
            SyntaxError,
            UnicodeError,
            ValueError,
            RecursionError,
        ):
            return None

    def profile(self, frame, event, _argument):
        if not self.active:
            # Threads started by the workload keep their installed callback.
            # Restore their prior inherited callback on the next event too.
            sys.setprofile(self.previous_thread_profile)
            return
        if event != "call":
            return
        code = frame.f_code
        if code.co_name.startswith("<"):
            return
        filename = Path(code.co_filename)
        if filename.suffix != ".py":
            return
        try:
            filename = filename.resolve()
            relative = filename.relative_to(self.root)
        except (OSError, ValueError, RuntimeError):
            return
        with self.lock:
            if filename not in self.sources:
                self.sources[filename] = self._source(filename)
            source = self.sources[filename]
            if source is None:
                return
            digest, names, identities = source
            qualified_name = getattr(
                code,
                "co_qualname",
                names.get((code.co_firstlineno, code.co_name)),
            )
            key = (code.co_firstlineno, qualified_name)
            if key not in identities:
                return
            location = relative.as_posix()
            observation_key = (location, *key)
            if (
                observation_key in self.observations
                or len(self.observations) >= _MAX_OBSERVATIONS
                or len(location) > _MAX_LOCATION_LENGTH
                or len(qualified_name) > _MAX_LOCATION_LENGTH
            ):
                return
            if identities[key] != _code_identity(code):
                return
            entry = {
                "path": location,
                "lineno": code.co_firstlineno,
                "qualname": qualified_name,
                "source_hash": digest,
            }
            # Account conservatively for enclosing indentation and separators.
            entry_bytes = len(json.dumps(entry, indent=2).encode("utf-8")) + 64
            if self.output_bytes + entry_bytes > _MAX_FILE_BYTES:
                return
            self.output_bytes += entry_bytes
            self.observations[observation_key] = entry


def record(output, project_root, *, module=None, script=None, args=None):
    """Execute one explicit workload, saving observations even if it fails.

    Targets have ordinary Python module/script argv and import paths. Profiles
    apply to this thread and subsequently created threads. Existing worker
    threads and other processes are not observed. Target exceptions propagate
    after evidence is written and interpreter state has been restored.
    """
    if (module is None) == (script is None):
        raise _RecorderError("Select exactly one runtime module or script")
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise _RecorderError(
            f"Runtime project root is not a directory: {root}"
        )
    output = Path(output).resolve()
    if output.is_dir():
        raise _RecorderError(
            f"Runtime evidence output is a directory: {output}"
        )
    if script is not None:
        script = Path(script).resolve()
        if not script.is_file():
            raise _RecorderError(f"Runtime script does not exist: {script}")
    if module is not None and not module:
        raise _RecorderError("Runtime module must not be empty")
    try:
        temporary = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output.parent,
            prefix=f".{output.name}.",
            delete=False,
        )
    except OSError as error:
        raise _RecorderError(
            f"Cannot write runtime evidence {output}: {error}"
        ) from error

    previous_profile = sys.getprofile()
    previous_thread_profile = threading.getprofile()
    previous_argv = sys.argv
    previous_path = sys.path
    previous_path_values = sys.path[:]
    recorder = _Recorder(root, previous_thread_profile)
    try:
        sys.argv = [module or str(script), *(args or [])]
        sys.path = [
            str(Path.cwd() if module is not None else script.parent),
            *previous_path_values[1:],
        ]
        threading.setprofile(recorder.profile)
        sys.setprofile(recorder.profile)
        if module is not None:
            runpy.run_module(module, run_name="__main__", alter_sys=True)
        else:
            runpy.run_path(str(script), run_name="__main__")
    finally:
        target_failed = sys.exc_info()[0] is not None
        recorder.active = False
        sys.setprofile(previous_profile)
        threading.setprofile(previous_thread_profile)
        sys.argv = previous_argv
        previous_path[:] = previous_path_values
        sys.path = previous_path
        try:
            with recorder.lock:
                observations = sorted(
                    recorder.observations.values(),
                    key=lambda entry: (
                        entry["path"],
                        entry["lineno"],
                        entry["qualname"],
                    ),
                )
            json.dump(
                {"version": _SCHEMA_VERSION, "observations": observations},
                temporary,
                indent=2,
            )
            temporary.write("\n")
            temporary.close()
            os.replace(temporary.name, output)
        except OSError as error:
            message = f"Cannot write runtime evidence {output}: {error}"
            if target_failed:
                print(message, file=sys.stderr)
            else:
                raise _RecorderError(message) from error
        finally:
            with suppress(OSError):
                temporary.close()
            with suppress(OSError):
                Path(temporary.name).unlink(missing_ok=True)


def main(args=None):
    arguments = list(sys.argv[1:] if args is None else args)
    target_args = []
    if "--" in arguments:
        separator = arguments.index("--")
        target_args = arguments[separator + 1 :]
        arguments = arguments[:separator]
    parser = argparse.ArgumentParser(
        description="Explicitly execute a workload and record runtime calls."
    )
    parser.add_argument("--output", required=True, metavar="FILE")
    parser.add_argument("--project-root", required=True, metavar="DIR")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--module", metavar="MODULE")
    target.add_argument("--script", metavar="FILE")
    options = parser.parse_args(arguments)
    try:
        record(
            options.output,
            options.project_root,
            module=options.module,
            script=options.script,
            args=target_args,
        )
    except _RecorderError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
