import subprocess
import sys
from io import StringIO

from voldemort import Voldemort
from voldemort.version import __version__
from vulture.utils import ExitCode

from . import REPO


def run_voldemort(*args):
    return subprocess.run(
        [sys.executable, "-m", "voldemort", *map(str, args)],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )


def make_app(tmp_path, source):
    app = tmp_path / "shop"
    app.mkdir()
    (app / "__init__.py").write_text("", encoding="utf-8")
    model = app / "models.py"
    model.write_text(source, encoding="utf-8")
    return model


def test_cli_keeps_scoped_findings_and_explains_retained_definitions(tmp_path):
    model = make_app(
        tmp_path,
        """from django.db import models
class Book(models.Model):
    title = models.CharField(max_length=200)
    def clean(self):
        spare = 42
class Ordinary:
    def clean(self):
        pass
""",
    )
    result = run_voldemort(
        model,
        "--project-root",
        tmp_path,
        "--django-app",
        "shop",
        "--explain",
    )

    assert result.returncode == ExitCode.DeadCode
    assert "unused variable 'spare'" in result.stdout
    assert "unused method 'clean'" in result.stdout
    assert "unused class 'Book'" not in result.stdout
    assert "unused variable 'title'" not in result.stdout
    assert "retained shop.models.Book.clean" in result.stderr
    assert "retained shop.models.Ordinary" not in result.stderr
    assert "retained" not in result.stdout


def test_cli_success_and_disabled_django_baseline(tmp_path):
    model = make_app(
        tmp_path,
        "from django.db import models\n"
        "class Book(models.Model):\n"
        "    title = models.CharField(max_length=200)\n"
        "    def clean(self):\n"
        "        pass\n",
    )
    args = (model, "--project-root", tmp_path, "--django-app", "shop")
    result = run_voldemort(*args)
    assert result.returncode == ExitCode.NoDeadCode
    assert not result.stdout
    assert not result.stderr

    result = run_voldemort(*args, "--no-django")
    assert result.returncode == ExitCode.DeadCode
    assert "unused class 'Book'" in result.stdout
    assert "unused method 'clean'" in result.stdout
    assert "unused variable 'title'" in result.stdout
    assert not result.stderr


def test_scanning_does_not_execute_project_code(tmp_path):
    model = make_app(
        tmp_path,
        "raise RuntimeError('must never be executed')\n"
        "from django.db import models\n"
        "class Book(models.Model):\n"
        "    title = models.CharField(max_length=200)\n",
    )
    result = run_voldemort(
        model, "--project-root", tmp_path, "--django-app", "shop"
    )
    assert result.returncode in {ExitCode.NoDeadCode, ExitCode.DeadCode}
    assert "Traceback" not in result.stderr
    assert "must never be executed" not in result.stderr


def test_cli_reads_voldemort_configuration(tmp_path):
    model = make_app(
        tmp_path,
        "from django.db import models\n"
        "class Book(models.Model):\n"
        "    title = models.CharField(max_length=200)\n",
    )
    config = tmp_path / "pyproject.toml"
    config.write_text(
        f'[tool.voldemort]\npaths = ["{model.as_posix()}"]\n'
        f'project_root = "{tmp_path.as_posix()}"\n'
        '[tool.voldemort.django]\napps = ["shop"]\n',
        encoding="utf-8",
    )
    result = run_voldemort("--config", config)
    assert result.returncode == ExitCode.NoDeadCode
    assert not result.stdout


def test_cli_reports_input_and_configuration_errors(tmp_path):
    result = run_voldemort(tmp_path / "missing.py", "--no-django")
    assert result.returncode == ExitCode.InvalidInput
    assert "Traceback" not in result.stderr

    result = run_voldemort("--config", tmp_path / "missing.toml")
    assert result.returncode == ExitCode.InvalidCmdlineArguments
    assert "Configuration file" in result.stderr
    assert "Traceback" not in result.stderr

    source = tmp_path / "broken.py"
    source.write_text("class :\n", encoding="utf-8")
    result = run_voldemort(source)
    assert result.returncode == ExitCode.InvalidInput
    assert "Traceback" not in result.stderr


def test_help_and_version_do_not_require_paths():
    result = run_voldemort("--version")
    assert result.returncode == ExitCode.NoDeadCode
    assert result.stdout.strip() == f"voldemort {__version__}"
    result = run_voldemort("--help")
    assert result.returncode == ExitCode.NoDeadCode
    assert "--django-app" in result.stdout
    assert "--explain" in result.stdout


def test_explanation_does_not_change_api_findings():
    analyzer = Voldemort()
    analyzer.scan(
        "from django import forms\n"
        "class Example(forms.Form):\n"
        "    title = forms.CharField()\n",
        filename="forms.py",
    )
    before = analyzer.get_unused_code()
    output = StringIO()
    analyzer.explain(file=output)
    assert "retained forms.Example.title" in output.getvalue()
    assert analyzer.get_unused_code() == before


def test_analysis_cache_tracks_django_options(tmp_path):
    analyzer = Voldemort(project_root=tmp_path)
    analyzer.scan(
        "from django.db import models\n"
        "class Book(models.Model):\n"
        "    def clean(self):\n"
        "        pass\n",
        filename=tmp_path / "shop" / "models.py",
    )
    assert not analyzer.unused_methods
    assert [item.name for item in analyzer.unused_classes] == ["Book"]

    analyzer.django_enabled = False
    assert not analyzer.django_enabled
    assert [item.name for item in analyzer.unused_methods] == ["clean"]
    assert not analyzer.django_evidence

    analyzer.django_enabled = True
    assert analyzer.django_enabled
    analyzer.django_apps.append("shop")
    assert not analyzer.unused_classes
    assert not analyzer.unused_methods

    analyzer.project_root = tmp_path / "elsewhere"
    assert analyzer.project_root == tmp_path / "elsewhere"
    assert [item.name for item in analyzer.unused_classes] == ["Book"]
    assert [item.name for item in analyzer.unused_methods] == ["clean"]


def test_hook_evidence_does_not_suppress_same_named_argument():
    analyzer = Voldemort()
    analyzer.scan(
        "from django.db import models\n"
        "class Book(models.Model):\n"
        "    def clean(self, clean):\n"
        "        pass\n",
        filename="models.py",
    )
    assert not analyzer.unused_methods
    assert [item.name for item in analyzer.unused_vars] == ["clean"]
