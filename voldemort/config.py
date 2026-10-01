"""CLI and ``[tool.voldemort]`` configuration for Django-aware analysis."""

import argparse
import keyword
import pathlib

from voldemort.version import __version__
from vulture.config import (
    DEFAULTS as VULTURE_DEFAULTS,
)
from vulture.config import (
    InputError,
    tomllib,
)
from vulture.config import (
    _check_input_config as _check_vulture_input,
)
from vulture.config import (
    _check_output_config as _check_vulture_output,
)

DEFAULTS = {
    **VULTURE_DEFAULTS,
    "django_enabled": True,
    "django_apps": [],
    "django_settings": "",
    "django_templates": [],
    "runtime_evidence": [],
    "project_root": ".",
    "explain": False,
}


def _check_input_config(data):
    """Validate flattened settings, including every list element."""
    _check_vulture_input(
        {key: value for key, value in data.items() if key in VULTURE_DEFAULTS}
    )
    for key, value in data.items():
        if key not in DEFAULTS:
            raise InputError(f"Unknown configuration key: {key}")
        if type(value) is not type(DEFAULTS[key]):
            expected_type = type(DEFAULTS[key]).__name__
            raise InputError(f"Data type for {key} must be {expected_type!r}")
        if isinstance(value, list) and any(
            not isinstance(element, str) for element in value
        ):
            raise InputError(f"All values in {key} must be strings")

    def dotted_name(value):
        return all(
            part.isidentifier() and not keyword.iskeyword(part)
            for part in value.split(".")
        )

    for app in data.get("django_apps", []):
        if not dotted_name(app):
            raise InputError(
                f"Invalid Django app {app!r}: use a dotted Python package "
                "or AppConfig class name"
            )
    settings = data.get("django_settings", "")
    if settings and not dotted_name(settings):
        raise InputError(
            f"Invalid Django settings module {settings!r}: "
            "use a dotted Python module name"
        )
    for key in ("django_templates", "runtime_evidence"):
        if any(not path.strip() for path in data.get(key, [])):
            raise InputError(f"Paths in {key} must not be empty")


def _parse_toml(infile):
    """Read only Voldemort's table; Vulture's settings remain independent."""
    try:
        data = tomllib.load(infile)
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as err:
        raise InputError(f"Invalid TOML configuration: {err}") from err

    tool = data.get("tool", {})
    if not isinstance(tool, dict):
        raise InputError("tool must be a TOML table")
    settings = tool.get("voldemort", {})
    if not isinstance(settings, dict):
        raise InputError("tool.voldemort must be a TOML table")
    settings = settings.copy()
    django = settings.pop("django", {})
    if not isinstance(django, dict):
        raise InputError("tool.voldemort.django must be a TOML table")
    for key in settings:
        if key not in {
            *VULTURE_DEFAULTS,
            "project_root",
            "explain",
            "runtime_evidence",
        }:
            raise InputError(f"Unknown configuration key: {key}")
    for key, value in django.items():
        if key not in {"enabled", "apps", "settings", "templates"}:
            raise InputError(f"Unknown Django configuration key: {key}")
        settings[f"django_{key}"] = value
    _check_input_config(settings)
    return settings


def _parse_args(argv=None):
    """Parse the upstream flags plus Voldemort's Django options."""
    parser = argparse.ArgumentParser(
        prog="voldemort",
        usage="%(prog)s [options] [PATH ...]",
        description="Find dead Python code with static Django support.",
        argument_default=argparse.SUPPRESS,
    )
    parser.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help="Python files or directories to analyze recursively.",
    )
    glob_help = "Patterns may contain glob wildcards (*, ?, [abc], [!abc])."
    for flag, help_text in (
        (
            "--exclude",
            "Comma-separated path patterns to ignore. Patterns match absolute "
            "paths; plain patterns are treated as *PATTERN*.",
        ),
        (
            "--ignore-decorators",
            "Comma-separated decorators whose functions and classes are "
            "ignored (e.g., @app.route,@require_*).",
        ),
        (
            "--ignore-names",
            "Comma-separated names to ignore (e.g., visit_*,do_*).",
        ),
    ):
        parser.add_argument(
            flag,
            metavar="PATTERNS",
            type=lambda value: value.split(","),
            help=f"{help_text} {glob_help}",
        )
    parser.add_argument(
        "--make-whitelist",
        action="store_true",
        help="Report unused code as a whitelist module.",
    )
    parser.add_argument(
        "--min-confidence",
        type=int,
        help="Minimum confidence (0 to 100) for unused-code reports.",
    )
    parser.add_argument(
        "--sort-by-size",
        action="store_true",
        help="Sort unused functions and classes by their lines of code.",
    )
    parser.add_argument(
        "--config", help="Path to the TOML configuration file."
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--no-django",
        dest="django_enabled",
        action="store_false",
        help="Disable Django and template rules; "
        "runtime evidence still applies.",
    )
    parser.add_argument(
        "--django-app",
        dest="django_apps",
        action="append",
        metavar="APP",
        help="Declare a Django app package or AppConfig class; may be "
        "repeated. Overrides apps discovered in INSTALLED_APPS.",
    )
    parser.add_argument(
        "--django-settings",
        metavar="MODULE",
        help="Select a Django settings module for static analysis.",
    )
    parser.add_argument(
        "--django-template",
        dest="django_templates",
        action="append",
        metavar="PATH",
        help="Analyze a Django template file or directory; may be repeated.",
    )
    parser.add_argument(
        "--runtime-evidence",
        action="append",
        metavar="JSON",
        help="Load recorded runtime evidence to retain observed calls only; "
        "may be repeated.",
    )
    parser.add_argument(
        "--project-root",
        metavar="DIR",
        help="Project directory used to resolve module names (default: .).",
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        help="Explain Django and runtime evidence exemptions on stderr.",
    )
    parser.add_argument(
        "--version", action="version", version=f"voldemort {__version__}"
    )
    config = vars(parser.parse_args(argv))
    _check_input_config(config)
    return config


def make_config(argv=None, tomlfile=None):
    """Merge defaults, TOML settings, and CLI options, in that order.

    ``tomlfile`` accepts a binary file-like object for embedding and tests.
    An absent default ``pyproject.toml`` is allowed; an explicitly requested
    configuration file must exist. Source-path existence is checked by Vulture
    when scanning, while ``project_root`` must be a directory. Template paths
    must name existing files or directories, and runtime evidence paths must
    name existing files. Paths are interpreted from the working directory.
    """
    cli_config = _parse_args(argv)
    detected_toml_path = ""
    try:
        if tomlfile is not None:
            file_config = _parse_toml(tomlfile)
            detected_toml_path = str(tomlfile)
        else:
            toml_path = pathlib.Path(
                cli_config.get("config", DEFAULTS["config"])
            )
            if toml_path.is_file():
                with toml_path.open("rb") as infile:
                    file_config = _parse_toml(infile)
                detected_toml_path = str(toml_path.resolve())
            elif "config" in cli_config or toml_path.exists():
                raise InputError(
                    f"Configuration file does not exist or is not a file: "
                    f"{toml_path}"
                )
            else:
                file_config = {}
    except OSError as err:
        raise InputError(f"Cannot read configuration file: {err}") from err

    # Copy mutable defaults so callers cannot alter later configurations.
    config = {
        key: value.copy() if isinstance(value, list) else value
        for key, value in DEFAULTS.items()
    }
    config.update(file_config)
    config.update(cli_config)
    _check_input_config(config)
    _check_vulture_output(config)
    if any(not path.strip() for path in config["paths"]):
        raise InputError("Analysis paths must not be empty")
    if (
        not config["project_root"].strip()
        or not pathlib.Path(config["project_root"]).is_dir()
    ):
        raise InputError("project_root must name an existing directory")
    for template in config["django_templates"]:
        path = pathlib.Path(template)
        if not (path.is_dir() or path.is_file()):
            raise InputError(
                f"Django template path does not name an existing file "
                f"or directory: {template}"
            )
    for evidence in config["runtime_evidence"]:
        if not pathlib.Path(evidence).is_file():
            raise InputError(
                f"Runtime evidence path does not name an existing file: "
                f"{evidence}"
            )
    if detected_toml_path and config["verbose"]:
        print(f"Reading configuration from {detected_toml_path}")
    return config
