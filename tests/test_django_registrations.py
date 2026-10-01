import ast
from textwrap import dedent, indent
from types import SimpleNamespace

import pytest

from voldemort import Voldemort
from voldemort.django import _Index
from voldemort.registrations import run


def analyze(tmp_path, sources, loaded=None, configs=()):
    modules = {
        tmp_path.joinpath(*name.split(".")).with_suffix(".py"): ast.parse(
            dedent(source)
        )
        for name, source in sources.items()
    }
    index = _Index(modules, tmp_path)
    discovery = SimpleNamespace(
        apps=["shop"],
        loaded_modules=set(sources) if loaded is None else set(loaded),
        admin_enabled=True,
        url_modules=set(),
        app_configs=[
            cls for cls in index.classes if cls.qualified_name in configs
        ],
    )
    run(index, discovery)
    return index.result


def retained(result, rule=None):
    return {
        evidence.qualified_name
        for evidence in result.evidence.values()
        if rule is None or evidence.rule == rule
    }


@pytest.mark.parametrize(
    ("imports", "decorator", "signal"),
    [
        (
            "from django.dispatch import receiver as hook\n"
            "from django.db.models.signals import post_save as saved",
            "hook",
            "saved",
        ),
        (
            "import django.dispatch as dispatch\n"
            "from django.core import signals",
            "dispatch.receiver",
            "signals.request_finished",
        ),
        (
            "from django.dispatch.dispatcher import receiver\n"
            "from django.contrib.auth import signals",
            "receiver",
            "signals.user_logged_in",
        ),
    ],
)
def test_receiver_decorator_import_aliases(
    tmp_path, imports, decorator, signal
):
    result = analyze(
        tmp_path,
        {
            "shop.signals": f"""\
                {imports}

                @{decorator}({signal})
                def saved(sender, **kwargs):
                    pass
            """.replace("                ", ""),
            "ordinary": """\
                def saved():
                    pass
            """,
        },
    )

    assert retained(result) == {"shop.signals.saved"}


def test_receiver_multiple_known_signals(tmp_path):
    result = analyze(
        tmp_path,
        {
            "signals": """\
                from django.dispatch import receiver
                from django.db.models.signals import post_save, post_delete

                @receiver([post_save, post_delete])
                def changed(sender, **kwargs):
                    pass
            """,
        },
    )

    assert retained(result) == {"signals.changed"}


@pytest.mark.parametrize("signals", ["[]", "unknown", "[post_save, unknown]"])
def test_unknown_receiver_signals_produce_notes_without_exemptions(
    tmp_path, signals
):
    result = analyze(
        tmp_path,
        {
            "signals": f"""\
                from django.dispatch import receiver
                from django.db.models.signals import post_save

                @receiver({signals})
                def changed(sender, **kwargs):
                    pass
            """,
        },
    )

    assert retained(result) == set()
    assert any(
        "Cannot resolve Django receiver signal" in n for n in result.notes
    )


def test_ordinary_receiver_decorator_is_not_django(tmp_path):
    result = analyze(
        tmp_path,
        {
            "signals": """\
                from other_library import receiver
                from django.db.models.signals import post_save

                @receiver(post_save)
                def changed(sender, **kwargs):
                    pass
            """,
        },
    )

    assert retained(result) == set()


def test_signal_declaration_is_not_a_module_reachability_claim(tmp_path):
    result = analyze(
        tmp_path,
        {
            "shop.unimported_signals": """\
                from django.dispatch import receiver
                from django.db.models.signals import post_save

                @receiver(post_save)
                def changed(sender, **kwargs):
                    pass
            """,
        },
        loaded=[],
    )

    assert retained(result) == {"shop.unimported_signals.changed"}
    assert (
        "when its module executes"
        in next(iter(result.evidence.values())).reason
    )


def test_local_custom_signal_and_alias(tmp_path):
    result = analyze(
        tmp_path,
        {
            "signals": """\
                from django.dispatch import Signal as Event, receiver as hook

                changed = Event()
                alias = changed

                @hook(alias)
                def notified(sender, **kwargs):
                    pass

                @hook(ordinary)
                def ignored(sender, **kwargs):
                    pass
            """,
        },
    )

    assert retained(result) == {"signals.notified"}


def test_custom_signal_imports_and_direct_connect(tmp_path):
    result = analyze(
        tmp_path,
        {
            "events": """\
                from django.dispatch import Signal
                changed = Signal()
            """,
            "callbacks": """\
                from django.dispatch import receiver
                from events import changed as event

                @receiver(event)
                def decorated(sender, **kwargs):
                    pass

                def direct(sender, **kwargs):
                    pass

                event.connect(receiver=direct)
            """,
        },
    )

    assert retained(result) == {"callbacks.decorated", "callbacks.direct"}


def test_shadowed_signal_is_not_recognized(tmp_path):
    result = analyze(
        tmp_path,
        {
            "signals": """\
                from django.dispatch import Signal, receiver
                event = Signal()
                event = unknown()

                @receiver(event)
                def changed(sender, **kwargs):
                    pass
            """,
        },
    )

    assert retained(result) == set()


def test_unused_function_containing_connect_does_not_register_receiver(
    tmp_path,
):
    result = analyze(
        tmp_path,
        {
            "signals": """\
                from django.db.models.signals import post_save

                def callback(sender, **kwargs):
                    pass

                def configure():
                    post_save.connect(callback)
            """,
        },
    )

    assert retained(result) == set()


def test_receiver_retains_only_function_and_leaves_body_and_argument(
    tmp_path,
):
    scanner = Voldemort(project_root=tmp_path)
    scanner.scan(
        dedent(
            """\
            from django.dispatch import receiver
            from django.db.models.signals import post_save

            @receiver(post_save)
            def changed(sender, changed):
                spare = 42

            class Ordinary:
                def changed(self):
                    pass
            """
        ),
        filename=tmp_path / "signals.py",
    )

    assert "changed" not in {item.name for item in scanner.unused_funcs}
    assert {
        (item.name, item.first_lineno) for item in scanner.unused_methods
    } == {("changed", 9)}
    assert {item.name for item in scanner.unused_vars} >= {
        "sender",
        "changed",
        "spare",
    }


def test_admin_decorator_retains_registered_class_and_scoped_callbacks(
    tmp_path,
):
    result = analyze(
        tmp_path,
        {
            "shop.admin": """\
                from django.contrib import admin as staff
                from django.db import models

                class Book(models.Model):
                    def model_display(self):
                        pass

                @staff.register(Book)
                class BookAdmin(staff.ModelAdmin):
                    list_display = ('admin_display', 'model_display')
                    readonly_fields = ('read_only',)
                    actions = ('publish',)

                    def admin_display(self, obj):
                        pass

                    def read_only(self, obj):
                        pass

                    def publish(self, request, queryset):
                        pass

                    @staff.display
                    def unused_display(self, obj):
                        pass

                    @staff.action
                    def unused_action(self, request, queryset):
                        pass

                class UnregisteredAdmin(staff.ModelAdmin):
                    list_display = ('admin_display',)

                    def admin_display(self, obj):
                        pass

                class Ordinary:
                    def publish(self):
                        pass
            """,
        },
    )

    assert retained(result) == {
        "shop.admin.BookAdmin",
        "shop.admin.BookAdmin.admin_display",
        "shop.admin.BookAdmin.read_only",
        "shop.admin.BookAdmin.publish",
        "shop.admin.Book.model_display",
    }


@pytest.mark.parametrize(
    "registration",
    [
        "staff.site.register(Book, BookAdmin)",
        "staff.site.register([Book], admin_class=BookAdmin)",
        "staff.register(Book)(BookAdmin)",
    ],
)
def test_direct_admin_registration_forms(tmp_path, registration):
    result = analyze(
        tmp_path,
        {
            "shop.admin": f"""\
                from django.contrib import admin as staff
                from django.db import models

                class Book(models.Model):
                    pass

                class BookAdmin(staff.ModelAdmin):
                    list_display = ('description',)

                    def description(self, obj):
                        pass

                {registration}
            """,
        },
    )

    assert retained(result) == {
        "shop.admin.BookAdmin",
        "shop.admin.BookAdmin.description",
    }


def test_admin_registration_requires_module_to_be_loaded(tmp_path):
    result = analyze(
        tmp_path,
        {
            "shop.admin": """\
                from django.contrib import admin
                from django.db import models

                class Book(models.Model):
                    pass

                @admin.register(Book)
                class BookAdmin(admin.ModelAdmin):
                    list_display = ('description',)

                    def description(self, obj):
                        pass
            """,
        },
        loaded=[],
    )

    assert retained(result) == set()


def test_custom_admin_site_instance_alias(tmp_path):
    result = analyze(
        tmp_path,
        {
            "shop.admin": """\
                from django.contrib.admin import AdminSite as Site, ModelAdmin
                from django.db import models

                class Book(models.Model):
                    pass

                class StaffSite(Site):
                    pass

                staff = StaffSite()
                alias = staff

                class BookAdmin(ModelAdmin):
                    actions = ('publish',)

                    def publish(self, request, queryset):
                        pass

                alias.register(Book, BookAdmin)
            """,
        },
    )

    assert retained(result) == {
        "shop.admin.BookAdmin",
        "shop.admin.BookAdmin.publish",
    }


def test_custom_admin_site_decorator(tmp_path):
    result = analyze(
        tmp_path,
        {
            "shop.admin": """\
                from django.contrib import admin
                from django.db import models

                class Book(models.Model):
                    pass

                staff = admin.AdminSite()

                @admin.register(Book, site=staff)
                class BookAdmin(admin.ModelAdmin):
                    pass

                @admin.register(Book, site=unknown)
                class UnknownSiteAdmin(admin.ModelAdmin):
                    pass
            """,
        },
    )

    assert retained(result) == {"shop.admin.BookAdmin"}


def test_admin_callback_lookup_prefers_fields_then_admin_then_model(tmp_path):
    result = analyze(
        tmp_path,
        {
            "shop.admin": """\
                from django.contrib import admin
                from django.db import models

                class Book(models.Model):
                    title = models.CharField(max_length=200)

                    def description(self):
                        pass

                    def read_only(self):
                        pass

                @admin.register(Book)
                class BookAdmin(admin.ModelAdmin):
                    list_display = ('title', 'description')
                    readonly_fields = ('read_only',)

                    def title(self, obj):
                        pass

                    def description(self, obj):
                        pass
            """,
        },
    )

    assert retained(result) == {
        "shop.admin.BookAdmin",
        "shop.admin.Book.title",
        "shop.admin.BookAdmin.description",
        "shop.admin.Book.read_only",
    }


def test_inherited_admin_callbacks_follow_method_resolution_order(tmp_path):
    result = analyze(
        tmp_path,
        {
            "shop.admin": """\
                from django.contrib import admin
                from django.db import models

                class Book(models.Model):
                    pass

                class DisplayMixin:
                    def description(self, obj):
                        pass

                class BaseAdmin(admin.ModelAdmin):
                    list_display = ('description',)

                    def description(self, obj):
                        pass

                @admin.register(Book)
                class BookAdmin(DisplayMixin, BaseAdmin):
                    pass
            """,
        },
    )

    assert retained(result) == {
        "shop.admin.BookAdmin",
        "shop.admin.DisplayMixin.description",
    }


def test_admin_actions_do_not_resolve_model_methods(tmp_path):
    result = analyze(
        tmp_path,
        {
            "shop.admin": """\
                from django.contrib import admin
                from django.db import models

                class Book(models.Model):
                    def publish(self):
                        pass

                @admin.register(Book)
                class BookAdmin(admin.ModelAdmin):
                    actions = ('publish',)
            """,
        },
    )

    assert retained(result) == {"shop.admin.BookAdmin"}


def test_admin_top_level_callable_and_global_site_action(tmp_path):
    result = analyze(
        tmp_path,
        {
            "shop.admin": """\
                from django.contrib import admin
                from django.db import models

                class Book(models.Model):
                    pass

                def description(obj):
                    pass

                def publish(modeladmin, request, queryset):
                    pass

                @admin.register(Book)
                class BookAdmin(admin.ModelAdmin):
                    list_display = (description,)
                    actions = ('publish_books',)

                admin.site.add_action(publish, name='publish_books')
            """,
        },
    )

    assert retained(result) == {
        "shop.admin.BookAdmin",
        "shop.admin.description",
        "shop.admin.publish",
    }


def test_admin_inline_and_readonly_callback(tmp_path):
    result = analyze(
        tmp_path,
        {
            "shop.admin": """\
                from django.contrib import admin
                from django.db import models

                class Book(models.Model):
                    pass

                class Chapter(models.Model):
                    pass

                class ChapterInline(admin.TabularInline):
                    model = Chapter
                    readonly_fields = ('summary',)

                    def summary(self, obj):
                        pass

                @admin.register(Book)
                class BookAdmin(admin.ModelAdmin):
                    inlines = (ChapterInline,)
            """,
        },
    )

    assert retained(result) == {
        "shop.admin.BookAdmin",
        "shop.admin.ChapterInline",
        "shop.admin.ChapterInline.summary",
    }


def test_prepopulated_fields_use_fields_without_retaining_same_name_methods(
    tmp_path,
):
    result = analyze(
        tmp_path,
        {
            "shop.admin": """\
                from django.contrib import admin
                from django.db import models

                class Book(models.Model):
                    title = models.CharField(max_length=200)
                    slug = models.SlugField()

                @admin.register(Book)
                class BookAdmin(admin.ModelAdmin):
                    prepopulated_fields = {'slug': ('title',)}

                    def title(self, obj):
                        pass

                    def slug(self, obj):
                        pass
            """,
        },
    )

    assert retained(result) == {
        "shop.admin.BookAdmin",
        "shop.admin.Book.title",
        "shop.admin.Book.slug",
    }


def test_dynamic_admin_options_produce_notes_without_retaining_callbacks(
    tmp_path,
):
    result = analyze(
        tmp_path,
        {
            "shop.admin": """\
                from django.contrib import admin
                from django.db import models

                class Book(models.Model):
                    pass

                @admin.register(Book)
                class BookAdmin(admin.ModelAdmin):
                    list_display = choose_columns()
                    actions = (unknown_action(),)
                    prepopulated_fields = choose_fields()

                    def unused(self, obj):
                        pass
            """,
        },
    )

    assert retained(result) == {"shop.admin.BookAdmin"}
    assert any("dynamic admin list_display" in n for n in result.notes)
    assert any("dynamic admin actions item" in n for n in result.notes)
    assert any("dynamic prepopulated_fields" in n for n in result.notes)


def test_receiver_before_later_signal_reassignment_keeps_its_snapshot(
    tmp_path,
):
    result = analyze(
        tmp_path,
        {
            "signals": """\
                from django.dispatch import Signal, receiver
                event = Signal()

                @receiver(event)
                def changed(sender, **kwargs):
                    pass

                event = unknown()
            """,
        },
    )

    assert retained(result) == {"signals.changed"}


def test_receiver_overwritten_by_later_definition_retains_original_only(
    tmp_path,
):
    result = analyze(
        tmp_path,
        {
            "signals": """\
                from django.dispatch import receiver
                from django.db.models.signals import post_save

                @receiver(post_save)
                def changed(sender, **kwargs):
                    pass

                def changed():
                    pass
            """,
        },
    )

    assert {
        (evidence.qualified_name, evidence.lineno)
        for evidence in result.evidence.values()
    } == {("signals.changed", 4)}


def test_imports_are_retained_only_for_actual_registration_side_effects(
    tmp_path,
):
    result = analyze(
        tmp_path,
        {
            "shop.models": """\
                from . import signals
                from . import unused_helpers
            """,
            "shop.signals": """\
                from django.dispatch import receiver
                from django.core.signals import request_finished

                @receiver(request_finished)
                def finished(sender, **kwargs):
                    pass
            """,
            "shop.unused_helpers": """\
                def unused():
                    pass
            """,
        },
    )

    assert retained(result, "django.registration-import") == {
        "shop.models.signals"
    }


def test_imports_can_reach_registration_through_another_module(tmp_path):
    result = analyze(
        tmp_path,
        {
            "shop.models": "from . import startup",
            "shop.startup": "import shop.signals as callbacks",
            "shop.signals": """\
                from django.dispatch import receiver
                from django.core.signals import request_finished

                @receiver(request_finished)
                def finished(sender, **kwargs):
                    pass
            """,
        },
    )

    assert retained(result, "django.registration-import") == {
        "shop.models.startup",
        "shop.startup.callbacks",
    }


def test_importing_unregistered_callback_module_does_not_register_it(tmp_path):
    result = analyze(
        tmp_path,
        {
            "shop.models": "from . import callbacks",
            "shop.callbacks": """\
                def finished(sender, **kwargs):
                    pass
            """,
            "shop.registrar": """\
                from django.core.signals import request_finished
                from .callbacks import finished
                request_finished.connect(finished)
            """,
        },
    )

    assert retained(result, "django.signal-receiver") == {
        "shop.callbacks.finished"
    }
    assert retained(result, "django.registration-import") == set()


def test_selected_ready_imports_preserve_only_known_registration_imports(
    tmp_path,
):
    sources = {
        "shop.__init__": "",
        "shop.apps": """\
            from django.apps import AppConfig

            class ShopConfig(AppConfig):
                name = 'shop'

                def ready(self):
                    from . import signals as callbacks
                    from . import unused_helpers

            class OtherConfig(AppConfig):
                default = False

                def ready(self):
                    from . import signals as unused_callbacks
        """,
        "shop.signals": """\
            from django.dispatch import receiver
            from django.core.signals import request_finished

            @receiver(request_finished)
            def finished(sender, **kwargs):
                pass
        """,
        "shop.unused_helpers": "unused = 1",
    }
    result = analyze(tmp_path, sources, configs=["shop.apps.ShopConfig"])

    assert retained(result, "django.registration-import") == {
        "shop.apps.ShopConfig.ready.callbacks"
    }

    scanner = Voldemort(project_root=tmp_path, django_apps=["shop"])
    for name, source in sources.items():
        scanner.scan(
            dedent(source),
            filename=tmp_path.joinpath(*name.split(".")).with_suffix(".py"),
        )
    assert {item.name for item in scanner.unused_imports} >= {
        "unused_helpers",
        "unused_callbacks",
    }
    assert "callbacks" not in {item.name for item in scanner.unused_imports}


def test_admin_callable_assignment_resolves_class_scope_at_assignment(
    tmp_path,
):
    result = analyze(
        tmp_path,
        {
            "shop.admin": """\
                from django.contrib import admin
                from django.db import models

                class Book(models.Model):
                    pass

                def display(obj):
                    pass

                @admin.register(Book)
                class BookAdmin(admin.ModelAdmin):
                    def display(self, obj):
                        pass

                    list_display = (display,)
            """,
        },
    )

    assert retained(result) == {
        "shop.admin.BookAdmin",
        "shop.admin.BookAdmin.display",
    }


def test_unresolved_callable_admin_class_does_not_crash_registration(tmp_path):
    result = analyze(
        tmp_path,
        {
            "shop.admin": """\
                from django.contrib import admin
                admin.register(dynamic_models())(make_admin())
            """,
        },
    )

    assert retained(result) == set()


def test_selected_ready_can_register_receiver_by_import_alias(tmp_path):
    sources = {
        "shop.__init__": "",
        "shop.apps": """\
            from django.apps import AppConfig

            class ShopConfig(AppConfig):
                name = 'shop'

                def ready(self):
                    from django.core.signals import request_finished
                    from .callbacks import finished as callback
                    request_finished.connect(callback)
        """,
        "shop.callbacks": """\
            def finished(sender, **kwargs):
                spare = 42
        """,
    }
    scanner = Voldemort(project_root=tmp_path, django_apps=["shop"])
    for name, source in sources.items():
        scanner.scan(
            dedent(source),
            filename=tmp_path.joinpath(*name.split(".")).with_suffix(".py"),
        )

    assert "finished" not in {item.name for item in scanner.unused_funcs}
    assert "spare" in {item.name for item in scanner.unused_vars}
    assert {
        item.qualified_name
        for item in scanner.django_evidence
        if item.rule == "django.signal-receiver"
    } == {"shop.callbacks.finished"}


@pytest.mark.parametrize(
    "termination",
    [
        "return",
        "raise RuntimeError()",
        "if True:\n    return",
        "if False:\n    pass\nelse:\n    return",
    ],
)
def test_selected_ready_does_not_retain_unreachable_registration_imports(
    tmp_path, termination
):
    source = (
        "from django.apps import AppConfig\n\n"
        "class ShopConfig(AppConfig):\n"
        "    name = 'shop'\n\n"
        "    def ready(self):\n"
        + indent(termination, "        ")
        + "\n        from . import signals as unreachable\n"
    )
    result = analyze(
        tmp_path,
        {
            "shop.apps": source,
            "shop.signals": """\
                from django.dispatch import receiver
                from django.core.signals import request_finished

                @receiver(request_finished)
                def finished(sender, **kwargs):
                    pass
            """,
        },
        configs=["shop.apps.ShopConfig"],
    )

    assert retained(result, "django.registration-import") == set()
