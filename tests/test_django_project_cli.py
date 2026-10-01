from textwrap import dedent

from voldemort import Voldemort
from vulture.utils import ExitCode

from .test_voldemort_cli import run_voldemort


def make_project(tmp_path):
    sources = {
        "project/__init__.py": "",
        "project/settings.py": """
            INSTALLED_APPS = ['django.contrib.admin', 'shop.apps.ShopConfig']
            ROOT_URLCONF = 'project.urls'
            MIDDLEWARE = ['shop.middleware.PlainMiddleware']
            AUTHENTICATION_BACKENDS = ['shop.auth.Backend']
            TEMPLATES = [
                {'OPTIONS': {'context_processors': ['shop.context.info']}}
            ]
        """,
        "project/urls.py": """
            from django.urls import include, path
            from shop.views import Books
            urlpatterns = [
                path('', Books.as_view()),
                path('shop/', include('shop.urls')),
            ]
        """,
        "shop/__init__.py": "",
        "shop/apps.py": """
            from django.apps import AppConfig
            class ShopConfig(AppConfig):
                name = 'shop'
                def ready(self):
                    from . import signals
        """,
        "shop/models.py": """
            from django.db import models
            class Book(models.Model):
                title = models.CharField(max_length=200)
                def clean(self):
                    spare_model = 42
        """,
        "shop/views.py": """
            from django.views.generic import ListView
            from .models import Book
            class Books(ListView):
                template_name = 'shop/books.html'
                def get_queryset(self):
                    spare_view = 42
                    return Book.objects.all()
            class Ordinary:
                def get_queryset(self):
                    pass
        """,
        "shop/urls.py": """
            app_name = 'shop'
            urlpatterns = []
        """,
        "shop/admin.py": """
            from django.contrib import admin
            from .models import Book
            @admin.register(Book)
            class BookAdmin(admin.ModelAdmin):
                list_display = ('display_title',)
                @admin.display
                def display_title(self, obj):
                    return obj.title
                @admin.display
                def unused_display(self, obj):
                    return obj.title
        """,
        "shop/signals.py": """
            from django.dispatch import receiver
            from django.db.models.signals import post_save
            @receiver(post_save)
            def on_save(sender, instance, **kwargs):
                spare_signal = 42
                return sender, instance, kwargs
        """,
        "shop/middleware.py": """
            class PlainMiddleware:
                def __init__(self, get_response):
                    self.get_response = get_response
                def __call__(self, request):
                    return self.get_response(request)
                def process_view(self, request, callback, args, kwargs):
                    return request, callback, args, kwargs
        """,
        "shop/auth.py": """
            class Backend:
                def authenticate(self, request, **credentials):
                    return request, credentials
                def get_user(self, user_id):
                    return user_id
        """,
        "shop/context.py": """
            def info(request):
                return {'request': request}
        """,
        "shop/management/commands/reindex.py": """
            from django.core.management.base import BaseCommand
            class Command(BaseCommand):
                help = 'Reindex books'
                def handle(self, *args, **options):
                    return args, options
            def unused_command_helper():
                pass
        """,
        "shop/migrations/0001_initial.py": """
            from django.db import migrations
            class Migration(migrations.Migration):
                initial = True
                dependencies = []
                operations = []
        """,
    }
    for relative, source in sources.items():
        filename = tmp_path / relative
        filename.parent.mkdir(parents=True, exist_ok=True)
        filename.write_text(dedent(source), encoding="utf-8")


def test_settings_driven_cli_covers_all_static_entry_points(tmp_path):
    make_project(tmp_path)
    result = run_voldemort(
        tmp_path,
        "--project-root",
        tmp_path,
        "--django-settings",
        "project.settings",
        "--explain",
    )
    assert result.returncode == ExitCode.DeadCode
    for name in (
        "spare_model",
        "spare_view",
        "spare_signal",
        "unused_display",
        "unused_command_helper",
        "get_queryset",
    ):
        assert f"'{name}'" in result.stdout
    for name in (
        "Book",
        "ShopConfig",
        "BookAdmin",
        "Backend",
        "PlainMiddleware",
        "Command",
        "Migration",
        "on_save",
        "info",
        "display_title",
        "process_view",
        "authenticate",
        "get_user",
        "urlpatterns",
        "app_name",
        "dependencies",
        "operations",
        "signals",
    ):
        assert f"'{name}'" not in result.stdout
    for target in (
        "shop.apps.ShopConfig",
        "shop.admin.BookAdmin.display_title",
        "shop.signals.on_save",
        "shop.auth.Backend.authenticate",
        "shop.management.commands.reindex.Command",
        "shop.migrations.0001_initial.Migration",
    ):
        assert f"retained {target}" in result.stderr
    assert "Traceback" not in result.stderr
    assert "retained" not in result.stdout


def test_settings_mode_can_be_disabled_without_explanations(tmp_path):
    make_project(tmp_path)
    result = run_voldemort(
        tmp_path,
        "--project-root",
        tmp_path,
        "--django-settings",
        "project.settings",
        "--no-django",
        "--explain",
    )
    assert result.returncode == ExitCode.DeadCode
    assert "'ShopConfig'" in result.stdout
    assert "'BookAdmin'" in result.stdout
    assert "'Command'" in result.stdout
    assert "'dependencies'" in result.stdout
    assert not result.stderr


def test_changing_settings_module_invalidates_api_evidence(tmp_path):
    make_project(tmp_path)
    analyzer = Voldemort(project_root=tmp_path)
    analyzer.scavenge([tmp_path])
    assert "Command" in {item.name for item in analyzer.unused_classes}
    analyzer.django_settings = "project.settings"
    assert analyzer.django_settings == "project.settings"
    assert "Command" not in {item.name for item in analyzer.unused_classes}
    analyzer.django_settings = ""
    assert "Command" in {item.name for item in analyzer.unused_classes}


def test_model_protocol_can_be_provided_after_model_base():
    analyzer = Voldemort()
    analyzer.scan(
        dedent(
            """
            from django.db import models
            class URLMixin:
                def get_absolute_url(self):
                    return '/book/'
            class Book(models.Model, URLMixin):
                pass
            class Ordinary:
                def get_absolute_url(self):
                    return '/ordinary/'
            """
        ),
        filename="models.py",
    )
    assert {item.first_lineno for item in analyzer.unused_methods} == {9}
