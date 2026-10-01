"""Vulture integration and the Voldemort command-line entry point."""

import sys
from pathlib import Path

from voldemort.config import make_config
from voldemort.django import Analysis, analyze
from vulture import Vulture
from vulture.config import InputError
from vulture.utils import ExitCode, format_path


class Voldemort(Vulture):
    """Add definition-specific Django evidence to Vulture's candidates.

    Django and project modules are never imported. ``django_settings`` selects
    a scanned settings module. ``django_apps`` overrides its app list, using
    packages or AppConfig paths relative to ``project_root``.
    """

    def __init__(
        self,
        verbose=False,
        ignore_names=None,
        ignore_decorators=None,
        *,
        django_enabled=True,
        django_apps=None,
        django_settings="",
        django_templates=None,
        runtime_evidence=None,
        project_root=None,
    ):
        super().__init__(
            verbose=verbose,
            ignore_names=ignore_names,
            ignore_decorators=ignore_decorators,
        )
        self.django_enabled = django_enabled
        self.django_apps = list(django_apps or [])
        self.django_settings = django_settings
        self.django_templates = list(django_templates or [])
        self.runtime_evidence_paths = list(runtime_evidence or [])
        self.project_root = Path(project_root or Path.cwd()).resolve()
        self._modules = {}
        self._source_texts = {}
        self._template_exclude = ()
        self._django_analysis = None
        self._django_analysis_config = None
        self._runtime_analysis = None
        self._runtime_analysis_config = None

    def scan(self, code, filename=""):
        self._modules.pop(Path(filename), None)
        self._source_texts[Path(filename)] = code
        self._django_analysis = None
        self._runtime_analysis = None
        super().scan(code, filename=filename)

    def scavenge(self, paths, exclude=None):
        self._template_exclude = tuple(
            pattern
            if any(char in pattern for char in "*?[")
            else f"*{pattern}*"
            for pattern in (exclude or [])
            if pattern
        )
        self._django_analysis = None
        super().scavenge(paths, exclude=exclude)

    def _scan_ast(self, node):
        self._modules[self.filename] = node
        super()._scan_ast(node)

    def _definition_node(self, node):
        return node

    def _get_django_analysis(self):
        config = (
            self.django_enabled,
            tuple(self.django_apps),
            Path(self.project_root).resolve(),
            self.django_settings,
            tuple(map(str, self.django_templates)),
            self._template_exclude,
        )
        if (
            self._django_analysis is None
            or config != self._django_analysis_config
        ):
            self._django_analysis = (
                analyze(
                    self._modules,
                    self.django_apps,
                    config[2],
                    settings_module=self.django_settings,
                    template_paths=self.django_templates,
                    template_exclude=self._template_exclude,
                )
                if self.django_enabled
                else Analysis({}, [])
            )
            self._django_analysis_config = config
        return self._django_analysis

    def _get_runtime_analysis(self):
        if not self.runtime_evidence_paths:
            return Analysis({}, [])
        from voldemort.runtime import load_evidence

        def stamp(filename):
            path = Path(filename).resolve()
            try:
                status = path.stat()
                return (
                    path,
                    status.st_mtime_ns,
                    status.st_ctime_ns,
                    status.st_size,
                )
            except OSError:
                return path, None, None, None

        config = (
            Path(self.project_root).resolve(),
            tuple(stamp(filename) for filename in self.runtime_evidence_paths),
            tuple(stamp(filename) for filename in self._modules),
        )
        if (
            self._runtime_analysis is None
            or config != self._runtime_analysis_config
        ):
            self._runtime_analysis = load_evidence(
                self.runtime_evidence_paths,
                self._modules,
                config[0],
                source_texts=self._source_texts,
            )
            self._runtime_analysis_config = config
        return self._runtime_analysis

    @property
    def django_evidence(self):
        """Sorted evidence explaining Django's implicit references."""
        return sorted(
            self._get_django_analysis().evidence.values(),
            key=lambda entry: (str(entry.filename), entry.lineno, entry.name),
        )

    @property
    def django_notes(self):
        """Coverage limitations encountered during static Django analysis."""
        return list(self._get_django_analysis().notes)

    @property
    def runtime_evidence(self):
        """Observed calls matched to exact, unchanged scanned definitions."""
        return sorted(
            self._get_runtime_analysis().evidence.values(),
            key=lambda entry: (str(entry.filename), entry.lineno, entry.name),
        )

    @property
    def runtime_notes(self):
        """Stale or unmatched runtime observations, without exemptions."""
        return list(self._get_runtime_analysis().notes)

    def _get_unused_items(self, defined_items):
        sources = (
            self._get_django_analysis().evidence,
            self._get_runtime_analysis().evidence,
        )

        def retained(item):
            # An argument can have the hook's name on its declaration line.
            # Keeping the hook must not suppress that distinct variable.
            return any(
                entry is not None
                and item.typ in entry.item_types
                and entry.node_id == id(item.definition_node)
                for entry in (
                    source.get((item.filename, item.first_lineno, item.name))
                    for source in sources
                )
            )

        if self.django_enabled or self.runtime_evidence_paths:
            # Vulture's historical equality joins same-line declarations by
            # name. Framework attributes and lambda arguments can share that
            # key, so preserve each AST declaration for scoped filtering.
            distinct = {}
            for item in defined_items:
                distinct.setdefault(
                    (
                        item.filename,
                        item.first_lineno,
                        item.name,
                        item.typ,
                        id(item.definition_node),
                    ),
                    item,
                )
            candidates = sorted(
                (
                    item
                    for item in distinct.values()
                    if item.name not in self.used_names
                ),
                key=lambda item: item.name.lower(),
            )
        else:
            candidates = super()._get_unused_items(defined_items)

        return [item for item in candidates if not retained(item)]

    def explain(self, file=None):
        """Write retained definitions and analysis coverage notes."""
        file = file or sys.stderr
        for entry in self.django_evidence + self.runtime_evidence:
            print(
                f"{format_path(entry.filename)}:{entry.lineno}: "
                f"retained {entry.qualified_name} [{entry.rule}]: "
                f"{entry.reason}",
                file=file,
            )
        for note in self.django_notes:
            print(f"Django coverage: {note}", file=file)
        for note in self.runtime_notes:
            print(f"Runtime coverage: {note}", file=file)


def main():
    try:
        config = make_config()
        analyzer = Voldemort(
            verbose=config["verbose"],
            ignore_names=config["ignore_names"],
            ignore_decorators=config["ignore_decorators"],
            django_enabled=config["django_enabled"],
            django_apps=config["django_apps"],
            django_settings=config["django_settings"],
            django_templates=config["django_templates"],
            runtime_evidence=config["runtime_evidence"],
            project_root=config["project_root"],
        )
        analyzer.scavenge(config["paths"], exclude=config["exclude"])
        if config["explain"]:
            analyzer.explain()
        else:
            for note in analyzer.django_notes:
                print(f"Django coverage: {note}", file=sys.stderr)
            for note in analyzer.runtime_notes:
                print(f"Runtime coverage: {note}", file=sys.stderr)
        exit_code = analyzer.report(
            min_confidence=config["min_confidence"],
            sort_by_size=config["sort_by_size"],
            make_whitelist=config["make_whitelist"],
        )
    except InputError as error:
        print(error, file=sys.stderr)
        sys.exit(ExitCode.InvalidCmdlineArguments)
    sys.exit(exit_code)
