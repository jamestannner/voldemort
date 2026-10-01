import ast
from textwrap import dedent

import pytest

from voldemort import Voldemort
from voldemort.discovery import discover
from voldemort.django import _Index


def index_modules(tmp_path, sources):
    return _Index(
        {
            tmp_path / filename: ast.parse(dedent(source))
            for filename, source in sources.items()
        },
        tmp_path,
    )


def retained(index, rule=None):
    return {
        evidence.qualified_name
        for evidence in index.result.evidence.values()
        if rule is None or evidence.rule == rule
    }


def test_settings_loader_retains_only_the_selected_uppercase_values(tmp_path):
    index = index_modules(
        tmp_path,
        {
            "project/settings.py": """
                INSTALLED_APPS = []
                SECRET_KEY = 'test'
                private_helper = 7
                raise RuntimeError('Do not execute settings')
            """,
            "project/unused_settings.py": """
                SECRET_KEY = 'inactive'
                OTHER_SETTING = 8
            """,
        },
    )

    result = discover(index, [], "project.settings")

    assert result.apps == []
    assert result.loaded_modules == {"project.settings"}
    assert retained(index, "django.settings-variable") == {
        "project.settings.INSTALLED_APPS",
        "project.settings.SECRET_KEY",
    }


def test_inactive_settings_variables_remain_reportable(tmp_path):
    scanner = Voldemort(
        project_root=tmp_path, django_settings="project.settings"
    )
    scanner.scan("SECRET_KEY = 'active'", tmp_path / "project/settings.py")
    scanner.scan(
        "INACTIVE_SECRET = 'inactive'",
        tmp_path / "project/unused_settings.py",
    )

    assert {item.name for item in scanner.unused_vars} == {"INACTIVE_SECRET"}
    assert "SECRET_KEY" not in scanner.used_names


def test_installed_apps_composition_and_base_star_import(tmp_path):
    index = index_modules(
        tmp_path,
        {
            "project/base.py": """
                BASE_APPS = ['shop']
                DEBUG = False
            """,
            "project/settings.py": """
                from .base import *
                EXTRA_APPS = ('accounts',)
                INSTALLED_APPS = [*BASE_APPS, *EXTRA_APPS]
                INSTALLED_APPS += ['payments']
            """,
            "shop/__init__.py": "",
            "accounts/__init__.py": "",
            "payments/__init__.py": "",
            "inactive/__init__.py": "",
        },
    )

    result = discover(index, [], "project.settings")

    assert result.apps == ["shop", "accounts", "payments"]
    assert {"project.base", "project.settings"} <= result.loaded_modules
    assert "inactive" not in result.loaded_modules
    assert {"project.base.BASE_APPS", "project.base.DEBUG"} <= retained(
        index, "django.settings-variable"
    )


def test_settings_import_alias_and_container_concatenation(tmp_path):
    index = index_modules(
        tmp_path,
        {
            "project/base.py": "BASE_APPS = ['shop']",
            "project/settings.py": """
                from . import base as common
                INSTALLED_APPS = common.BASE_APPS + ['accounts']
            """,
            "shop/__init__.py": "",
            "accounts/__init__.py": "",
        },
    )

    assert discover(index, [], "project.settings").apps == [
        "shop",
        "accounts",
    ]


def test_settings_imported_dotted_module_provides_literal_apps(tmp_path):
    index = index_modules(
        tmp_path,
        {
            "project/__init__.py": "",
            "project/base.py": "BASE_APPS = ['shop']",
            "project/settings.py": """
                import project.base
                INSTALLED_APPS = project.base.BASE_APPS
            """,
            "shop/__init__.py": "",
        },
    )

    assert discover(index, [], "project.settings").apps == ["shop"]


def test_explicit_apps_override_computed_installed_apps(tmp_path):
    index = index_modules(
        tmp_path,
        {
            "project/settings.py": "INSTALLED_APPS = get_apps()",
            "shop/__init__.py": "",
            "accounts/__init__.py": "",
        },
    )

    result = discover(index, ["shop"], "project.settings")

    assert result.apps == ["shop"]
    assert "accounts" not in result.loaded_modules
    assert not any("INSTALLED_APPS" in note for note in index.result.notes)


@pytest.mark.parametrize(
    "settings",
    [
        "INSTALLED_APPS = get_apps()",
        "INSTALLED_APPS = ['shop']\nINSTALLED_APPS.append(computed_app)",
        "INSTALLED_APPS = ['shop']\nif debug:\n    INSTALLED_APPS = ['other']",
    ],
)
def test_computed_or_mutated_apps_produce_coverage_notes(tmp_path, settings):
    index = index_modules(
        tmp_path,
        {
            "project/settings.py": settings,
            "shop/__init__.py": "",
            "other/__init__.py": "",
        },
    )

    assert discover(index, [], "project.settings").apps == []
    assert any("INSTALLED_APPS" in note for note in index.result.notes)


def test_mutating_an_alias_invalidates_installed_apps(tmp_path):
    index = index_modules(
        tmp_path,
        {
            "project/settings.py": """
                base_apps = ['shop']
                INSTALLED_APPS = base_apps
                base_apps.clear()
            """,
            "shop/__init__.py": "",
        },
    )

    assert discover(index, [], "project.settings").apps == []
    assert any("INSTALLED_APPS" in note for note in index.result.notes)


def test_deleted_installed_apps_do_not_activate_an_app(tmp_path):
    index = index_modules(
        tmp_path,
        {
            "project/settings.py": """
                INSTALLED_APPS = ['shop']
                del INSTALLED_APPS
            """,
            "shop/__init__.py": "",
        },
    )

    assert discover(index, [], "project.settings").apps == []


@pytest.mark.parametrize(
    "conditional",
    [
        "match computed:\n    case INSTALLED_APPS:\n        pass",
        "match computed:\n    case [*INSTALLED_APPS]:\n        pass",
        """\
        match computed:
            case {'known': _, **INSTALLED_APPS}:
                pass
        """,
        """\
        try:
            unknown_call()
        except Exception as INSTALLED_APPS:
            pass
        """,
    ],
)
def test_conditional_capture_names_invalidate_literal_apps(
    tmp_path, conditional
):
    source = "INSTALLED_APPS = ['shop']\n" + dedent(conditional)
    index = index_modules(
        tmp_path,
        {
            "project/settings.py": source,
            "shop/__init__.py": "",
        },
    )

    assert discover(index, [], "project.settings").apps == []
    assert any("INSTALLED_APPS" in note for note in index.result.notes)


def test_unknown_apps_are_reported_without_scanning_unrelated_packages(
    tmp_path,
):
    index = index_modules(
        tmp_path,
        {"shop/__init__.py": "", "other/__init__.py": ""},
    )

    result = discover(index, ["shop", "missing"])

    assert result.apps == ["shop"]
    assert result.loaded_modules == {"shop"}
    assert any("missing" in note for note in index.result.notes)


def test_explicit_appconfig_uses_inherited_name_and_ready(tmp_path):
    index = index_modules(
        tmp_path,
        {
            "shared.py": """
                from django.apps import AppConfig as Configuration

                class BaseConfig(Configuration):
                    name = 'shop'
                    default = False

                    def ready(self):
                        import shop.receivers
            """,
            "shop/__init__.py": "",
            "shop/apps.py": """
                from shared import BaseConfig

                class ShopConfig(BaseConfig):
                    pass
            """,
            "shop/receivers.py": "",
            "inactive/apps.py": """
                from django.apps import AppConfig

                class InactiveConfig(AppConfig):
                    name = 'inactive'
            """,
        },
    )

    result = discover(index, ["shop.apps.ShopConfig"])

    assert result.apps == ["shop"]
    assert [cls.qualified_name for cls in result.app_configs] == [
        "shop.apps.ShopConfig"
    ]
    assert "shop.receivers" in result.loaded_modules
    assert retained(index, "django.app-config") == {"shop.apps.ShopConfig"}


def test_automatic_appconfig_selection_respects_inherited_default_false(
    tmp_path,
):
    index = index_modules(
        tmp_path,
        {
            "shared.py": """
                from django.apps import AppConfig

                class BaseConfig(AppConfig):
                    default = False
                    name = 'shop'
            """,
            "shop/__init__.py": "",
            "shop/apps.py": """
                from shared import BaseConfig

                class DisabledConfig(BaseConfig):
                    pass

                class SelectedConfig(BaseConfig):
                    default = True
            """,
        },
    )

    result = discover(index, ["shop"])

    assert [cls.qualified_name for cls in result.app_configs] == [
        "shop.apps.SelectedConfig"
    ]
    assert retained(index, "django.app-config") == {"shop.apps.SelectedConfig"}


def test_appconfig_default_false_leaves_package_without_selected_config(
    tmp_path,
):
    index = index_modules(
        tmp_path,
        {
            "shop/__init__.py": "",
            "shop/apps.py": """
                from django.apps import AppConfig

                class DisabledConfig(AppConfig):
                    name = 'shop'
                    default = False

                    def ready(self):
                        import shop.disabled_receivers
            """,
            "shop/disabled_receivers.py": "",
        },
    )

    result = discover(index, ["shop"])

    assert result.apps == ["shop"]
    assert result.app_configs == []
    assert "shop.disabled_receivers" not in result.loaded_modules
    assert retained(index, "django.app-config") == set()


def test_multiple_default_appconfigs_have_no_broad_exemption(tmp_path):
    index = index_modules(
        tmp_path,
        {
            "shop/__init__.py": "",
            "shop/apps.py": """
                from django.apps import AppConfig

                class FirstConfig(AppConfig):
                    name = 'shop'
                    default = True

                class SecondConfig(AppConfig):
                    name = 'shop'
                    default = True
            """,
        },
    )

    result = discover(index, ["shop"])

    assert result.app_configs == []
    assert retained(index, "django.app-config") == set()
    assert any("Multiple default" in note for note in index.result.notes)


def test_ready_follows_unconditional_imports_and_literal_branches_only(
    tmp_path,
):
    index = index_modules(
        tmp_path,
        {
            "shop/__init__.py": "",
            "shop/apps.py": """
                from django.apps import AppConfig

                class ShopConfig(AppConfig):
                    name = 'shop'

                    def ready(self):
                        from . import receivers
                        if True:
                            import shop.literal_receivers
                        if False:
                            import shop.false_receivers
                        if runtime_condition:
                            import shop.conditional_receivers
                        def deferred():
                            import shop.deferred_receivers
            """,
            "shop/receivers.py": "import shop.receiver_dependency",
            "shop/receiver_dependency.py": "",
            "shop/literal_receivers.py": "",
            "shop/false_receivers.py": "",
            "shop/conditional_receivers.py": "",
            "shop/deferred_receivers.py": "",
        },
    )

    result = discover(index, ["shop"])

    assert {
        "shop.receivers",
        "shop.receiver_dependency",
        "shop.literal_receivers",
    } <= result.loaded_modules
    assert (
        not {
            "shop.false_receivers",
            "shop.conditional_receivers",
            "shop.deferred_receivers",
        }
        & result.loaded_modules
    )
    assert any("Conditional imports" in note for note in index.result.notes)


@pytest.mark.parametrize(
    "terminator",
    [
        "return",
        "raise RuntimeError('stopped')",
        "if True:\n            return",
    ],
)
def test_ready_imports_after_return_or_raise_are_not_loaded(
    tmp_path, terminator
):
    apps = (
        "from django.apps import AppConfig\n"
        "class ShopConfig(AppConfig):\n"
        "    name = 'shop'\n"
        "    def ready(self):\n"
        f"        {terminator}\n"
        "        from . import spare\n"
    )
    index = index_modules(
        tmp_path,
        {
            "shop/__init__.py": "",
            "shop/apps.py": apps,
            "shop/spare.py": "",
        },
    )

    result = discover(index, ["shop"])

    assert result.apps == ["shop"]
    assert "shop.spare" not in result.loaded_modules


def test_known_settings_strings_retain_exact_definitions(tmp_path):
    index = index_modules(
        tmp_path,
        {
            "project/settings.py": """
                MIDDLEWARE = ['shop.runtime.SiteMiddleware']
                AUTHENTICATION_BACKENDS = ['shop.runtime.SiteBackend']
                WSGI_APPLICATION = 'shop.runtime.application'
                TEMPLATES = [{
                    'OPTIONS': {
                        'context_processors': ['shop.runtime.context'],
                    },
                }]
                CUSTOM_STRING = 'shop.runtime.unrelated'
            """,
            "shop/runtime.py": """
                class SiteMiddleware:
                    pass

                class SiteBackend:
                    pass

                application = object()

                def context(request):
                    return {}

                def unrelated():
                    pass
            """,
            "unrelated.py": """
                class SiteMiddleware:
                    pass

                def context(request):
                    return {}
            """,
        },
    )

    result = discover(index, [], "project.settings")

    assert retained(index, "django.settings-reference") == {
        "shop.runtime.SiteMiddleware",
        "shop.runtime.SiteBackend",
        "shop.runtime.application",
        "shop.runtime.context",
    }
    assert "shop.runtime" in result.loaded_modules
    assert "unrelated" not in result.loaded_modules


def test_urlconf_aliases_string_handlers_and_cyclic_includes(tmp_path):
    index = index_modules(
        tmp_path,
        {
            "project/settings.py": "ROOT_URLCONF = 'project.urls'",
            "project/urls.py": """
                from django.urls import include as route_include, path

                urlpatterns = [
                    path(
                        'shop/',
                        route_include('shop.urls', namespace='shop'),
                    ),
                ]
                handler404 = 'shop.views.not_found'
                CUSTOM_HANDLER = 'shop.views.unused_handler'
            """,
            "shop/urls.py": """
                from django.urls import include, path

                app_name = 'shop'
                urlpatterns = [path('', include('project.urls'))]
            """,
            "shop/views.py": """
                def not_found(request, exception):
                    pass

                def unused_handler(request, exception):
                    pass
            """,
            "inactive/urls.py": "urlpatterns = []",
        },
    )

    result = discover(index, [], "project.settings")

    assert result.url_modules == {"project.urls", "shop.urls"}
    assert retained(index, "django.url-variable") == {
        "project.urls.urlpatterns",
        "project.urls.handler404",
        "shop.urls.urlpatterns",
        "shop.urls.app_name",
    }
    assert retained(index, "django.url-reference") == {"shop.views.not_found"}


def test_url_discovery_skips_unrelated_includes_and_deferred_branches(
    tmp_path,
):
    index = index_modules(
        tmp_path,
        {
            "project/settings.py": "ROOT_URLCONF = 'project.urls'",
            "project/urls.py": """
                from django.urls import include, path
                from helper import include as unrelated_include

                urlpatterns = [
                    path('other/', unrelated_include('inactive.urls')),
                ]
                if runtime_condition:
                    urlpatterns += [path('', include('conditional.urls'))]
                if False:
                    include('false.urls')
                def build_urls():
                    return include('deferred.urls')
            """,
            "helper.py": "def include(value):\n    return value",
            "inactive/urls.py": "urlpatterns = []",
            "conditional/urls.py": "urlpatterns = []",
            "false/urls.py": "urlpatterns = []",
            "deferred/urls.py": "urlpatterns = []",
        },
    )

    result = discover(index, [], "project.settings")

    assert result.url_modules == {"project.urls"}
    assert not any(
        name.endswith(".urlpatterns") and name != "project.urls.urlpatterns"
        for name in retained(index)
    )


def test_namespace_app_and_module_url_include_are_discovered(tmp_path):
    index = index_modules(
        tmp_path,
        {
            "project/settings.py": "ROOT_URLCONF = 'project.urls'",
            "project/urls.py": """
                from django.urls import include, path
                from shop import urls as shop_routes

                urlpatterns = [path('', include(shop_routes))]
            """,
            "shop/urls.py": "urlpatterns = []",
        },
    )

    result = discover(index, ["shop"], "project.settings")

    assert result.apps == ["shop"]
    assert result.url_modules == {"project.urls", "shop.urls"}


def test_include_tuple_and_alias_use_bindings_at_the_call(tmp_path):
    index = index_modules(
        tmp_path,
        {
            "project/settings.py": "ROOT_URLCONF = 'project.urls'",
            "project/urls.py": """
                from django.urls import include
                route_module = 'shop.urls'
                urlpatterns = [include((route_module, 'shop'))]
                route_module = 'inactive.urls'
                include = object()
            """,
            "shop/urls.py": "urlpatterns = []",
            "inactive/urls.py": "urlpatterns = []",
        },
    )

    result = discover(index, [], "project.settings")

    assert result.url_modules == {"project.urls", "shop.urls"}


def test_configured_protocols_follow_aliases_without_retaining_other_hooks(
    tmp_path,
):
    index = index_modules(
        tmp_path,
        {
            "project/settings.py": """
                MIDDLEWARE = ['shop.exports.Middleware']
                AUTHENTICATION_BACKENDS = ['shop.exports.Backend']
                ASGI_APPLICATION = 'shop.exports.asgi'
            """,
            "shop/exports.py": """
                from .runtime import (
                    PlainMiddleware as Middleware,
                    PlainBackend as Backend,
                    application as asgi,
                )
            """,
            "shop/runtime.py": """
                class PlainMiddleware:
                    sync_capable = True
                    async_capable = False

                    def process_view(self, request, callback, args, kwargs):
                        pass

                    def process_exception(self, request, exception):
                        pass

                    def process_request(self, request):
                        pass

                    def process_response(self, request, response):
                        pass

                class PlainBackend:
                    def authenticate(self, request, **credentials):
                        pass

                    def get_user(self, user_id):
                        pass

                    def unrelated(self):
                        pass

                def application(scope, receive, send):
                    pass
            """,
            "other.py": """
                class PlainMiddleware:
                    def process_view(self):
                        pass

                class PlainBackend:
                    def authenticate(self):
                        pass
            """,
        },
    )

    result = discover(index, [], "project.settings")

    assert retained(index, "django.settings-reference") == {
        "shop.exports.Middleware",
        "shop.exports.Backend",
        "shop.exports.asgi",
        "shop.runtime.PlainMiddleware",
        "shop.runtime.PlainBackend",
        "shop.runtime.application",
    }
    assert retained(index, "django.framework-hook") == {
        "shop.runtime.PlainMiddleware.process_view",
        "shop.runtime.PlainMiddleware.process_exception",
        "shop.runtime.PlainBackend.authenticate",
        "shop.runtime.PlainBackend.get_user",
    }
    assert retained(index, "django.framework-attribute") == {
        "shop.runtime.PlainMiddleware.sync_capable",
        "shop.runtime.PlainMiddleware.async_capable",
    }
    assert {"shop.exports", "shop.runtime"} <= result.loaded_modules


def test_only_active_direct_command_modules_retain_command_classes(tmp_path):
    index = index_modules(
        tmp_path,
        {
            "shop/__init__.py": "",
            "shop/management/commands/export.py": """
                from django.core.management.base import BaseCommand as Base

                class Command(Base):
                    def handle(self, *args, **options):
                        pass

                class HelperCommand(Base):
                    pass
            """,
            "shop/management/commands/fake.py": """
                class Command:
                    def handle(self):
                        pass
            """,
            "shop/management/commands/_private.py": """
                from django.core.management.base import BaseCommand
                class Command(BaseCommand):
                    pass
            """,
            "shop/management/commands/package/__init__.py": """
                from django.core.management.base import BaseCommand
                class Command(BaseCommand):
                    pass
            """,
            "shop/management/commands/package/nested.py": """
                from django.core.management.base import BaseCommand
                class Command(BaseCommand):
                    pass
            """,
            "inactive/management/commands/export.py": """
                from django.core.management.base import BaseCommand
                class Command(BaseCommand):
                    pass
            """,
        },
    )

    result = discover(index, ["shop"])

    assert retained(index, "django.command") == {
        "shop.management.commands.export.Command"
    }
    assert {
        "shop.management.commands.export",
        "shop.management.commands.fake",
    } <= result.loaded_modules
    assert (
        not {
            "shop.management.commands._private",
            "shop.management.commands.package",
            "shop.management.commands.package.nested",
            "inactive.management.commands.export",
        }
        & result.loaded_modules
    )


def test_migration_package_overrides_use_configured_app_label(tmp_path):
    index = index_modules(
        tmp_path,
        {
            "project/settings.py": """
                INSTALLED_APPS = ['shop.apps.ShopConfig']
                MIGRATION_MODULES = {'catalog': 'history.catalog'}
            """,
            "shop/__init__.py": "",
            "shop/apps.py": """
                from django.apps import AppConfig

                class ShopConfig(AppConfig):
                    name = 'shop'
                    label = 'catalog'
            """,
            "history/catalog/0001_initial.py": """
                from django.db import migrations

                class Migration(migrations.Migration):
                    dependencies = []
                    operations = []
            """,
            "shop/migrations/0001_initial.py": """
                from django.db import migrations
                class Migration(migrations.Migration):
                    pass
            """,
        },
    )

    result = discover(index, [], "project.settings")

    assert retained(index, "django.migration") == {
        "history.catalog.0001_initial.Migration"
    }
    assert "history.catalog.0001_initial" in result.loaded_modules
    assert "shop.migrations.0001_initial" not in result.loaded_modules


def test_disabled_migrations_and_fake_classes_are_not_retained(tmp_path):
    index = index_modules(
        tmp_path,
        {
            "project/settings.py": "MIGRATION_MODULES = {'shop': None}",
            "shop/__init__.py": "",
            "accounts/__init__.py": "",
            "shop/migrations/0001_initial.py": """
                from django.db import migrations
                class Migration(migrations.Migration):
                    pass
            """,
            "accounts/migrations/0001_initial.py": """
                class Migration:
                    operations = []
            """,
        },
    )

    result = discover(index, ["shop", "accounts"], "project.settings")

    assert retained(index, "django.migration") == set()
    assert "shop.migrations.0001_initial" not in result.loaded_modules
    assert "accounts.migrations.0001_initial" in result.loaded_modules


def test_historical_model_lookup_does_not_retain_current_model(tmp_path):
    index = index_modules(
        tmp_path,
        {
            "shop/__init__.py": "",
            "shop/models.py": """
                from django.db import models
                class Book(models.Model):
                    pass
            """,
            "shop/migrations/0001_initial.py": """
                from django.db import migrations

                def forwards(apps, schema_editor):
                    Book = apps.get_model('shop', 'Book')

                class Migration(migrations.Migration):
                    operations = [migrations.RunPython(forwards)]

                class HelperMigration(migrations.Migration):
                    pass
            """,
        },
    )

    discover(index, ["shop"])

    assert retained(index, "django.migration") == {
        "shop.migrations.0001_initial.Migration"
    }
    assert "shop.models.Book" not in retained(index)


def test_namespace_app_selects_its_scanned_app_config(tmp_path):
    index = index_modules(
        tmp_path,
        {
            "shop/apps.py": """
                from django.apps import AppConfig
                class ShopConfig(AppConfig):
                    name = 'shop'
                    def ready(self):
                        from . import signals
            """,
            "shop/signals.py": "registered = True",
            "shop/spare.py": "spare = True",
        },
    )

    result = discover(index, ["shop"])

    assert result.apps == ["shop"]
    assert retained(index, "django.app-config") == {"shop.apps.ShopConfig"}
    assert "shop.signals" in result.loaded_modules
    assert "shop.spare" not in result.loaded_modules


@pytest.mark.parametrize(
    ("base", "autodiscovery"),
    [("AdminConfig", True), ("SimpleAdminConfig", False)],
)
def test_admin_config_inherits_name_and_selects_exact_custom_site(
    tmp_path, base, autodiscovery
):
    index = index_modules(
        tmp_path,
        {
            "project/apps.py": f"""
                from django.contrib.admin.apps import {base}
                class CustomAdminConfig({base}):
                    default_site = 'project.sites.CustomSite'
            """,
            "project/sites.py": """
                from django.contrib.admin import AdminSite
                class CustomSite(AdminSite):
                    pass
                class UnselectedSite(AdminSite):
                    pass
            """,
            "shop/__init__.py": "",
            "shop/admin.py": "registered = True",
        },
    )

    result = discover(index, ["project.apps.CustomAdminConfig", "shop"])

    assert result.apps == ["django.contrib.admin", "shop"]
    assert result.admin_enabled is autodiscovery
    assert retained(index, "django.app-config-reference") == {
        "project.sites.CustomSite"
    }
    assert "project.sites" in result.loaded_modules
    assert ("shop.admin" in result.loaded_modules) is autodiscovery


@pytest.mark.parametrize(
    ("exports", "expected_apps"),
    [
        ("['OTHER']", []),
        ("['INSTALLED_APPS']", ["shop"]),
        ("()", []),
    ],
)
def test_base_settings_star_import_respects_literal_exports(
    tmp_path, exports, expected_apps
):
    index = index_modules(
        tmp_path,
        {
            "project/base.py": f"""
                INSTALLED_APPS = ['shop']
                OTHER = 42
                __all__ = {exports}
            """,
            "project/settings.py": "from .base import *",
            "shop/__init__.py": "",
        },
    )

    result = discover(index, [], "project.settings")

    assert result.apps == expected_apps
    assert ("project.base.INSTALLED_APPS" in retained(index)) is bool(
        expected_apps
    )


def test_computed_base_settings_exports_do_not_infer_installed_apps(tmp_path):
    index = index_modules(
        tmp_path,
        {
            "project/base.py": """
                INSTALLED_APPS = ['shop']
                __all__ = choose_exports()
            """,
            "project/settings.py": "from .base import *",
            "shop/__init__.py": "",
        },
    )

    result = discover(index, [], "project.settings")

    assert result.apps == []
    assert "project.base.INSTALLED_APPS" not in retained(index)
    assert any("star import exports" in note for note in index.result.notes)
