"""Configuration contracts for the Django-aware command."""

from io import BytesIO
from textwrap import dedent

import pytest

from voldemort.config import DEFAULTS, InputError, _parse_args, make_config
from voldemort.version import __version__


def toml(text):
    return BytesIO(dedent(text).encode())


def test_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = make_config(["src"])
    assert config == {**DEFAULTS, "paths": ["src"]}
    config["django_apps"].append("changed")
    config["django_templates"].append("changed")
    config["runtime_evidence"].append("changed")
    config["ignore_names"].append("changed")
    assert make_config(["src"])["django_apps"] == []
    assert make_config(["src"])["django_templates"] == []
    assert make_config(["src"])["runtime_evidence"] == []
    assert make_config(["src"])["ignore_names"] == []


def test_cli_preserves_common_options_and_adds_django_options(tmp_path):
    config = make_config(
        [
            "--exclude=build,*.test.py",
            "--ignore-decorators=@route,@ignore_*",
            "--ignore-names=unused_*,spare",
            "--make-whitelist",
            "--min-confidence=90",
            "--sort-by-size",
            "-v",
            "--no-django",
            "--django-app=shop",
            "--django-app=project.accounts",
            "--django-settings=project.settings",
            "--project-root",
            str(tmp_path),
            "--explain",
            "src",
            "tests",
        ],
        toml(""),
    )
    assert config == {
        **DEFAULTS,
        "paths": ["src", "tests"],
        "exclude": ["build", "*.test.py"],
        "ignore_decorators": ["@route", "@ignore_*"],
        "ignore_names": ["unused_*", "spare"],
        "make_whitelist": True,
        "min_confidence": 90,
        "sort_by_size": True,
        "verbose": True,
        "django_enabled": False,
        "django_apps": ["shop", "project.accounts"],
        "django_settings": "project.settings",
        "project_root": str(tmp_path),
        "explain": True,
    }


def test_toml_options_and_cli_precedence(tmp_path):
    config = make_config(
        [
            "--no-django",
            "--django-app=cli.app",
            "--django-settings=cli.settings",
            "--exclude=cli",
            "--project-root",
            str(tmp_path),
            "--min-confidence=80",
            "--explain",
            "cli.py",
        ],
        toml(
            """
            [tool.voldemort]
            paths = ['toml.py']
            exclude = ['toml']
            ignore_names = ['configured']
            project_root = '.'
            min_confidence = 50
            explain = false
            [tool.voldemort.django]
            enabled = true
            apps = ['toml.app', 'another']
            settings = 'toml.settings'
            """
        ),
    )
    assert config["paths"] == ["cli.py"]
    assert config["exclude"] == ["cli"]
    assert config["ignore_names"] == ["configured"]
    assert config["django_enabled"] is False
    assert config["django_apps"] == ["cli.app"]
    assert config["django_settings"] == "cli.settings"
    assert config["project_root"] == str(tmp_path)
    assert config["min_confidence"] == 80
    assert config["explain"] is True


def test_toml_values_survive_missing_cli_options():
    config = make_config(
        [],
        toml(
            """
            [tool.voldemort]
            paths = ['src']
            verbose = true
            explain = true
            [tool.voldemort.django]
            enabled = false
            apps = ['shop']
            settings = 'project.settings'
            """
        ),
    )
    assert config["paths"] == ["src"]
    assert config["verbose"] is True
    assert config["explain"] is True
    assert config["django_enabled"] is False
    assert config["django_apps"] == ["shop"]
    assert config["django_settings"] == "project.settings"


def test_upstream_toml_table_is_not_used():
    config = make_config(
        ["src"],
        toml(
            """
            [tool.vulture]
            paths = ['other']
            min_confidence = 99
            ignore_names = ['*']
            """
        ),
    )
    assert config["paths"] == ["src"]
    assert config["min_confidence"] == 0
    assert config["ignore_names"] == []


@pytest.mark.parametrize(
    "text, message",
    [
        ("[tool.voldemort]\nunknown = true", "Unknown configuration key"),
        (
            "[tool.voldemort.django]\nunknown = true",
            "Unknown Django configuration key",
        ),
        ("tool = 'bad'", "tool must be a TOML table"),
        ("[tool]\nvoldemort = 'bad'", "voldemort must be a TOML table"),
        (
            "[tool.voldemort]\ndjango = true",
            "django must be a TOML table",
        ),
        ("[tool.voldemort]\nmin_confidence = true", "must be 'int'"),
        ("[tool.voldemort]\npaths = ['src', 1]", "must be strings"),
        ("[tool.voldemort]\nexclude = [false]", "must be strings"),
        ("[tool.voldemort]\nignore_names = [1]", "must be strings"),
        ("[tool.voldemort]\nignore_decorators = [{}]", "must be strings"),
        ("[tool.voldemort]\nproject_root = false", "must be 'str'"),
        ("[tool.voldemort]\nexplain = 1", "must be 'bool'"),
        ("[tool.voldemort]\nruntime_evidence = 'run.json'", "must be 'list'"),
        ("[tool.voldemort]\nruntime_evidence = [1]", "must be strings"),
        ("[tool.voldemort]\nruntime_evidence = ['']", "must not be empty"),
        ("[tool.voldemort]\nruntime_evidence = ['  ']", "must not be empty"),
        ("[tool.voldemort.django]\nenabled = 1", "must be 'bool'"),
        ("[tool.voldemort.django]\napps = 'shop'", "must be 'list'"),
        ("[tool.voldemort.django]\napps = [1]", "must be strings"),
        ("[tool.voldemort.django]\nsettings = 1", "must be 'str'"),
        ("[tool.voldemort.django]\nsettings = false", "must be 'str'"),
        ("[tool.voldemort.django]\nsettings = []", "must be 'str'"),
        ("[tool.voldemort.django]\ntemplates = 'templates'", "must be 'list'"),
        ("[tool.voldemort.django]\ntemplates = [false]", "must be strings"),
        ("[tool.voldemort.django]\ntemplates = ['']", "must not be empty"),
        ("[tool.voldemort.django]\ntemplates = ['  ']", "must not be empty"),
        (
            "[tool.voldemort]\ndjango_enabled = true",
            "Unknown configuration key",
        ),
        (
            "[tool.voldemort]\ndjango_settings = 'project.settings'",
            "Unknown configuration key",
        ),
        (
            "[tool.voldemort]\ndjango_templates = []",
            "Unknown configuration key",
        ),
        ("[tool.voldemort", "Invalid TOML configuration"),
    ],
)
def test_invalid_toml_is_an_input_error(text, message):
    with pytest.raises(InputError, match=message):
        make_config(["src"], toml(text))


@pytest.mark.parametrize(
    "app", ["", ".shop", "shop.", "a..b", "my-app", "class"]
)
def test_invalid_django_app_names(app):
    with pytest.raises(InputError, match="Invalid Django app"):
        make_config([f"--django-app={app}", "src"], toml(""))
    with pytest.raises(InputError, match="Invalid Django app"):
        make_config(
            ["src"], toml(f"[tool.voldemort.django]\napps = ['{app}']")
        )


def test_explicit_app_config_class():
    config = make_config(
        ["--django-app=shop.apps.ShopConfig", "src"], toml("")
    )
    assert config["django_apps"] == ["shop.apps.ShopConfig"]


@pytest.mark.parametrize(
    "settings", [" ", "project. settings", ".settings", "a..b", "a.class"]
)
def test_invalid_settings_module(settings):
    with pytest.raises(InputError, match="Invalid Django settings module"):
        make_config([f"--django-settings={settings}", "src"], toml(""))
    with pytest.raises(InputError, match="Invalid Django settings module"):
        make_config(
            ["src"],
            toml(f"[tool.voldemort.django]\nsettings = '{settings}'"),
        )


@pytest.mark.parametrize("settings", ["", "settings", "project.settings.dev"])
def test_valid_settings_module(settings):
    config = make_config([f"--django-settings={settings}", "src"], toml(""))
    assert config["django_settings"] == settings
    config = make_config(
        ["src"], toml(f"[tool.voldemort.django]\nsettings = '{settings}'")
    )
    assert config["django_settings"] == settings


def test_explicit_config_file(tmp_path):
    config_file = tmp_path / "custom.toml"
    config_file.write_text(
        "[tool.voldemort]\npaths = ['src']\n"
        "[tool.voldemort.django]\napps = ['shop']"
    )
    config = make_config(["--config", str(config_file)])
    assert config["django_apps"] == ["shop"]
    assert config["config"] == str(config_file)


def test_explicit_missing_config_file(tmp_path):
    with pytest.raises(InputError, match="Configuration file"):
        make_config(["--config", str(tmp_path / "missing.toml"), "src"])


def test_config_directory_is_rejected(tmp_path):
    with pytest.raises(InputError, match="Configuration file"):
        make_config(["--config", str(tmp_path), "src"])


def test_default_config_is_loaded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.voldemort]\npaths = ['configured.py']"
    )
    assert make_config([])["paths"] == ["configured.py"]


@pytest.mark.parametrize("confidence", [-1, 101])
def test_confidence_range(confidence):
    with pytest.raises(InputError, match="between 0 and 100"):
        make_config([f"--min-confidence={confidence}", "src"], toml(""))


@pytest.mark.parametrize("confidence", [0, 100])
def test_confidence_range_boundaries(confidence):
    config = make_config([f"--min-confidence={confidence}", "src"], toml(""))
    assert config["min_confidence"] == confidence


def test_missing_paths():
    with pytest.raises(InputError, match="at least one file or directory"):
        make_config([], toml(""))


@pytest.mark.parametrize("path", ["", "   "])
def test_empty_paths(path):
    with pytest.raises(InputError, match="must not be empty"):
        make_config([path], toml(""))


def test_invalid_project_root(tmp_path):
    with pytest.raises(InputError, match="existing directory"):
        make_config(
            ["--project-root", str(tmp_path / "missing"), "src"], toml("")
        )
    file_path = tmp_path / "file.py"
    file_path.write_text("")
    with pytest.raises(InputError, match="existing directory"):
        make_config(["--project-root", str(file_path), "src"], toml(""))


@pytest.mark.parametrize("flag", ["--help", "--version"])
def test_help_and_version_are_branded_without_loading_config(
    flag, tmp_path, capsys
):
    with pytest.raises(SystemExit) as err:
        make_config(["--config", str(tmp_path / "missing.toml"), flag])
    assert err.value.code == 0
    output = capsys.readouterr().out
    assert "voldemort" in output
    if flag == "--version":
        assert output.strip() == f"voldemort {__version__}"
    else:
        assert "--django-app" in output
        assert "--django-settings" in output
        assert "--django-template" in output
        assert "--runtime-evidence" in output
        assert "--explain" in output


def test_parse_args_omits_unspecified_settings():
    assert _parse_args([]) == {}


def test_repeatable_template_and_evidence_options(tmp_path):
    templates = tmp_path / "templates"
    templates.mkdir()
    template = tmp_path / "index.html"
    template.write_text("{{ object.title }}")
    first_evidence = tmp_path / "first.json"
    first_evidence.write_text("{}")
    second_evidence = tmp_path / "second.json"
    second_evidence.write_text("{}")
    config = make_config(
        [
            "--no-django",
            "--django-template",
            str(templates),
            "--django-template",
            str(template),
            "--runtime-evidence",
            str(first_evidence),
            "--runtime-evidence",
            str(second_evidence),
            "src",
        ],
        toml(""),
    )
    assert config["django_templates"] == [str(templates), str(template)]
    assert config["runtime_evidence"] == [
        str(first_evidence),
        str(second_evidence),
    ]
    assert config["django_enabled"] is False


def test_template_and_evidence_toml_defaults_and_cli_precedence(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    for name in ("toml-templates", "cli-templates"):
        (tmp_path / name).mkdir()
    for name in ("toml.json", "cli.json"):
        (tmp_path / name).write_text("{}")
    text = """
        [tool.voldemort]
        paths = ['src']
        runtime_evidence = ['toml.json']
        [tool.voldemort.django]
        templates = ['toml-templates']
        """
    config = make_config([], toml(text))
    assert config["django_templates"] == ["toml-templates"]
    assert config["runtime_evidence"] == ["toml.json"]
    config = make_config(
        [
            "--django-template=cli-templates",
            "--runtime-evidence=cli.json",
        ],
        toml(text),
    )
    assert config["django_templates"] == ["cli-templates"]
    assert config["runtime_evidence"] == ["cli.json"]


@pytest.mark.parametrize("flag", ["--django-template", "--runtime-evidence"])
@pytest.mark.parametrize("path", ["", "   "])
def test_empty_template_and_evidence_paths(flag, path):
    with pytest.raises(InputError, match="must not be empty"):
        make_config([f"{flag}={path}", "src"], toml(""))


@pytest.mark.parametrize("source", ["cli", "toml"])
def test_missing_template_path_is_rejected(tmp_path, source):
    path = tmp_path / "missing"
    args = ["src"]
    text = ""
    if source == "cli":
        args.append(f"--django-template={path}")
    else:
        text = f"[tool.voldemort.django]\ntemplates = ['{path}']"
    with pytest.raises(InputError, match=r"template path.*existing"):
        make_config(args, toml(text))


@pytest.mark.parametrize("source", ["cli", "toml"])
@pytest.mark.parametrize("path_kind", ["missing", "directory"])
def test_runtime_evidence_path_must_be_a_file(tmp_path, source, path_kind):
    path = tmp_path / "evidence"
    if path_kind == "directory":
        path.mkdir()
    args = ["src"]
    text = ""
    if source == "cli":
        args.append(f"--runtime-evidence={path}")
    else:
        text = f"[tool.voldemort]\nruntime_evidence = ['{path}']"
    with pytest.raises(InputError, match=r"evidence path.*existing file"):
        make_config(args, toml(text))


def test_cli_overrides_unavailable_configured_paths(tmp_path):
    template = tmp_path / "template.html"
    template.write_text("")
    evidence = tmp_path / "evidence.json"
    evidence.write_text("{}")
    config = make_config(
        [
            f"--django-template={template}",
            f"--runtime-evidence={evidence}",
            "src",
        ],
        toml(
            """
            [tool.voldemort]
            runtime_evidence = ['unavailable.json']
            [tool.voldemort.django]
            templates = ['unavailable-templates']
            """
        ),
    )
    assert config["django_templates"] == [str(template)]
    assert config["runtime_evidence"] == [str(evidence)]
