import json
import os
import subprocess
import sys
from textwrap import dedent

import pytest

from vulture.utils import ExitCode

from . import REPO
from .test_voldemort_cli import run_voldemort


def write_sources(root, sources):
    for relative, source in sources.items():
        filename = root / relative
        filename.parent.mkdir(parents=True, exist_ok=True)
        filename.write_text(dedent(source).lstrip("\n"), encoding="utf-8")


def make_template_project(root):
    marker = root / "project-was-imported"
    write_sources(
        root,
        {
            "project/__init__.py": "",
            "project/settings.py": """
                INSTALLED_APPS = ['shop']
                ROOT_URLCONF = 'project.urls'
            """,
            "project/urls.py": """
                from django.urls import path
                from shop.views import BookList
                urlpatterns = [path('book/', BookList.as_view())]
            """,
            "shop/__init__.py": f"""
                from pathlib import Path
                Path({str(marker)!r}).write_text('imported')
                raise RuntimeError('static analysis executed project code')
            """,
            "shop/models.py": """
                from django.db import models
                class Book(models.Model):
                    title = models.CharField(max_length=200)
                    def render_label(self):
                        dead_template_local = 42
                        return 'Book'
                    def unused_model_helper(self):
                        return None
                class Ordinary:
                    def render_label(self):
                        return 'Ordinary'
            """,
            "shop/views.py": """
                from django.views.generic import ListView
                from .models import Book
                class BookList(ListView):
                    model = Book
                    context_object_name = 'books'
                    template_name = 'shop/book.html'
            """,
            "shop/templatetags/__init__.py": "",
            "shop/templatetags/catalog.py": """
                from django import template
                register = template.Library()
                @register.simple_tag(name='badge')
                def render_badge(book):
                    return book
                @register.filter(name='presentation')
                def render_presentation(value):
                    return value
                @register.simple_tag(name='unused_badge')
                def unused_tag_helper():
                    return None
            """,
            "templates/shop/book.html": """
                {% load catalog %}
                {% for book in books %}
                {{ book.render_label }}
                {% badge book %}
                {{ book|presentation }}
                {% endfor %}
            """,
        },
    )
    return marker


def make_runtime_project(root):
    write_sources(
        root,
        {
            "events.py": """
                def passthrough(function):
                    return function
                @passthrough
                def observed():
                    dead_observed_local = 42
                    return 'called'
                def never_called():
                    return None
                class Service:
                    def invoked(self):
                        dead_method_local = 43
                        return 'called'
                    def ignored(self):
                        return None
            """,
            "runner.py": """
                import sys
                import events
                assert sys.argv[1:] == ['token']
                assert getattr(events, 'observed')() == 'called'
                service = getattr(events, 'Service')()
                assert getattr(service, 'invoked')() == 'called'
            """,
        },
    )
    return root / "events.py", root / "evidence.json"


def record_runtime(root, output, *, module=False):
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(
            None,
            [str(REPO), str(root), environment.get("PYTHONPATH", "")],
        )
    )
    target = (
        ["--module", "runner"] if module else ["--script", root / "runner.py"]
    )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "voldemort.runtime",
            "--output",
            str(output),
            "--project-root",
            str(root),
            *map(str, target),
            "--",
            "token",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_template_cli_retains_exact_model_and_registered_callbacks(tmp_path):
    marker = make_template_project(tmp_path)
    result = run_voldemort(
        tmp_path,
        "--project-root",
        tmp_path,
        "--django-settings",
        "project.settings",
        "--django-template",
        tmp_path / "templates",
        "--explain",
    )
    assert result.returncode == ExitCode.DeadCode
    assert not marker.exists()
    for name in (
        "dead_template_local",
        "unused_model_helper",
        "unused_tag_helper",
        "render_label",
    ):
        assert f"'{name}'" in result.stdout
    for target in (
        "shop.models.Book.render_label",
        "shop.templatetags.catalog.render_badge",
        "shop.templatetags.catalog.render_presentation",
    ):
        assert f"retained {target}" in result.stderr
    assert "retained shop.models.Ordinary.render_label" not in result.stderr
    assert "'render_badge'" not in result.stdout
    assert "'render_presentation'" not in result.stdout
    assert "Traceback" not in result.stderr
    assert "retained" not in result.stdout


@pytest.mark.parametrize("module", [False, True], ids=["script", "module"])
def test_runtime_cli_retains_observed_calls_with_django_disabled(
    tmp_path, module
):
    source, output = make_runtime_project(tmp_path)
    recording = record_runtime(tmp_path, output, module=module)
    assert recording.returncode == 0, recording.stderr
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert any(
        item["path"] == "events.py"
        and item["qualname"] == "observed"
        and item["lineno"] == 3
        for item in data["observations"]
    )
    result = run_voldemort(
        source,
        "--project-root",
        tmp_path,
        "--no-django",
        "--runtime-evidence",
        output,
        "--explain",
    )
    assert result.returncode == ExitCode.DeadCode
    for name in (
        "dead_observed_local",
        "dead_method_local",
        "never_called",
        "ignored",
    ):
        assert f"'{name}'" in result.stdout
    for name in ("observed", "invoked"):
        assert f"'{name}'" not in result.stdout
    for target in ("events.observed", "events.Service.invoked"):
        assert f"retained {target}" in result.stderr
    assert "retained" not in result.stdout
    assert "Traceback" not in result.stderr


def test_stale_runtime_source_reports_calls_without_retaining_them(tmp_path):
    source, output = make_runtime_project(tmp_path)
    recording = record_runtime(tmp_path, output)
    assert recording.returncode == 0, recording.stderr
    source.write_text(
        source.read_text(encoding="utf-8") + "\n", encoding="utf-8"
    )
    result = run_voldemort(
        source,
        "--project-root",
        tmp_path,
        "--no-django",
        "--runtime-evidence",
        output,
        "--explain",
    )
    assert result.returncode == ExitCode.DeadCode
    assert "'observed'" in result.stdout
    assert "'invoked'" in result.stdout
    assert "retained events.observed" not in result.stderr
    assert "Runtime coverage:" in result.stderr
    assert "hash" in result.stderr.lower() or "stale" in result.stderr.lower()
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    "content",
    [
        "{invalid json",
        '{"version": 999, "observations": []}',
        '{"version": 1, "observations": [{"path": "events.py"}]}',
    ],
)
def test_malformed_runtime_evidence_is_a_cli_error(tmp_path, content):
    source, output = make_runtime_project(tmp_path)
    output.write_text(content, encoding="utf-8")
    result = run_voldemort(
        source,
        "--project-root",
        tmp_path,
        "--runtime-evidence",
        output,
    )
    assert result.returncode == ExitCode.InvalidCmdlineArguments
    assert result.stderr
    assert "Traceback" not in result.stderr


def test_missing_template_path_is_a_cli_error(tmp_path):
    source, _ = make_runtime_project(tmp_path)
    result = run_voldemort(
        source,
        "--django-template",
        tmp_path / "missing-templates",
    )
    assert result.returncode == ExitCode.InvalidCmdlineArguments
    assert "template" in result.stderr.lower()
    assert "Traceback" not in result.stderr


def test_template_and_runtime_evidence_combine_in_one_cli_scan(tmp_path):
    marker = make_template_project(tmp_path)
    _, output = make_runtime_project(tmp_path)
    recording = record_runtime(tmp_path, output)
    assert recording.returncode == 0, recording.stderr
    result = run_voldemort(
        tmp_path,
        "--project-root",
        tmp_path,
        "--django-settings",
        "project.settings",
        "--django-template",
        tmp_path / "templates" / "shop" / "book.html",
        "--runtime-evidence",
        output,
        "--explain",
    )
    assert result.returncode == ExitCode.DeadCode
    assert not marker.exists()
    for target in (
        "shop.models.Book.render_label",
        "shop.templatetags.catalog.render_badge",
        "events.observed",
        "events.Service.invoked",
    ):
        assert f"retained {target}" in result.stderr
    assert "'never_called'" in result.stdout
    assert "'unused_tag_helper'" in result.stdout
    assert "'dead_template_local'" in result.stdout
    assert "Traceback" not in result.stderr


def test_repeated_cli_paths_merge_template_and_runtime_evidence(tmp_path):
    marker = make_template_project(tmp_path)
    _, output = make_runtime_project(tmp_path)
    extra_template = tmp_path / "additional.html"
    main_template = tmp_path / "templates" / "shop" / "book.html"
    main_template.write_text(
        "{% load catalog %}{% for book in books %}"
        "{{ book.render_label }}{% badge book %}{% endfor %}",
        encoding="utf-8",
    )
    extra_template.write_text(
        "{% load catalog %}{{ value|presentation }}", encoding="utf-8"
    )
    recording = record_runtime(tmp_path, output)
    assert recording.returncode == 0, recording.stderr
    data = json.loads(output.read_text(encoding="utf-8"))
    extra_output = tmp_path / "extra-evidence.json"
    extra_output.write_text(
        json.dumps(
            {
                **data,
                "observations": [
                    entry
                    for entry in data["observations"]
                    if entry["qualname"] == "Service.invoked"
                ],
            }
        ),
        encoding="utf-8",
    )
    data["observations"] = [
        entry
        for entry in data["observations"]
        if entry["qualname"] != "Service.invoked"
    ]
    output.write_text(json.dumps(data), encoding="utf-8")
    result = run_voldemort(
        tmp_path,
        "--project-root",
        tmp_path,
        "--django-settings",
        "project.settings",
        "--django-template",
        main_template,
        "--django-template",
        extra_template,
        "--runtime-evidence",
        output,
        "--runtime-evidence",
        extra_output,
        "--explain",
    )
    assert result.returncode == ExitCode.DeadCode
    assert not marker.exists()
    for target in (
        "shop.models.Book.render_label",
        "shop.templatetags.catalog.render_presentation",
        "events.observed",
        "events.Service.invoked",
    ):
        assert f"retained {target}" in result.stderr
    assert "'ignored'" in result.stdout
    assert "'dead_method_local'" in result.stdout
    assert "Traceback" not in result.stderr
