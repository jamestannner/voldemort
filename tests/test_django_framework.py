from textwrap import dedent

import pytest

from voldemort import Voldemort, framework


def scan(source, **kwargs):
    scanner = Voldemort(**kwargs)
    scanner.scan(dedent(source), filename="example.py")
    return scanner


def definitions(items):
    return {(item.name, item.first_lineno) for item in items}


@pytest.mark.parametrize(
    ("module", "base", "hook"),
    [
        ("django.views", "View", "get"),
        ("django.views.generic", "View", "dispatch"),
        ("django.views.generic.base", "View", "setup"),
        ("django.views.generic.base", "View", "options"),
        ("django.views.generic", "TemplateView", "get_context_data"),
        ("django.views.generic", "TemplateView", "get_template_names"),
        ("django.views.generic", "RedirectView", "get_redirect_url"),
        ("django.views.generic", "DetailView", "get_queryset"),
        ("django.views.generic", "ListView", "get_queryset"),
        ("django.views.generic", "ListView", "paginate_queryset"),
        ("django.views.generic", "FormView", "form_valid"),
        ("django.views.generic", "CreateView", "get_queryset"),
        ("django.views.generic", "UpdateView", "form_invalid"),
        ("django.views.generic", "DeleteView", "get_success_url"),
        ("django.views.generic.base", "ContextMixin", "get_context_data"),
        (
            "django.views.generic.base",
            "TemplateResponseMixin",
            "render_to_response",
        ),
        ("django.views.generic.detail", "SingleObjectMixin", "get_object"),
        ("django.views.generic.list", "MultipleObjectMixin", "get_ordering"),
        ("django.views.generic.edit", "FormMixin", "get_form_kwargs"),
        ("django.views.generic.edit", "ModelFormMixin", "get_queryset"),
        ("django.views.generic.edit", "ProcessFormView", "put"),
        ("django.views.generic.edit", "DeletionMixin", "delete"),
        ("django.apps", "AppConfig", "ready"),
        ("django.apps.config", "AppConfig", "import_models"),
        ("django.contrib.admin.apps", "AdminConfig", "ready"),
        ("django.core.management.base", "BaseCommand", "handle"),
        ("django.core.management.base", "BaseCommand", "add_arguments"),
        ("django.core.management.base", "AppCommand", "handle_app_config"),
        ("django.core.management.base", "LabelCommand", "handle_label"),
        ("django.db.migrations", "Migration", "apply"),
        ("django.db.migrations.migration", "Migration", "mutate_state"),
        ("django.contrib.admin", "ModelAdmin", "get_queryset"),
        ("django.contrib.admin", "ModelAdmin", "save_model"),
        ("django.contrib.admin", "ModelAdmin", "get_search_results"),
        ("django.contrib.admin.options", "ModelAdmin", "get_formset_kwargs"),
        ("django.contrib.admin", "StackedInline", "get_extra"),
        ("django.contrib.admin", "TabularInline", "get_formset"),
        ("django.contrib.admin.options", "InlineModelAdmin", "get_queryset"),
        ("django.contrib.admin", "AdminSite", "each_context"),
        ("django.contrib.admin.sites", "AdminSite", "get_app_list"),
        ("django.utils.deprecation", "MiddlewareMixin", "process_view"),
        ("django.contrib.auth.backends", "BaseBackend", "authenticate"),
        ("django.contrib.auth.backends", "ModelBackend", "ahas_perm"),
        (
            "django.contrib.auth.backends",
            "RemoteUserBackend",
            "clean_username",
        ),
    ],
)
def test_framework_hooks_are_scoped(module, base, hook):
    scanner = scan(
        f"""\
        from {module} import {base} as FrameworkBase

        class Component(FrameworkBase):
            def {hook}(self):
                spare = 1

            def unused_helper(self):
                pass

        class Ordinary:
            def {hook}(self):
                pass
        """
    )

    assert definitions(scanner.unused_methods) == {
        ("unused_helper", 7),
        (hook, 11),
    }
    assert ("spare", 5) in definitions(scanner.unused_vars)
    assert {item.name for item in scanner.unused_classes} == {
        "Component",
        "Ordinary",
    }
    assert hook not in scanner.used_names
    evidence = [
        entry for entry in scanner.django_evidence if entry.name == hook
    ]
    assert len(evidence) == 1
    assert evidence[0].rule == "django.framework-hook"


@pytest.mark.parametrize(
    ("module", "base", "attribute"),
    [
        ("django.views", "View", "http_method_names"),
        ("django.views.generic", "TemplateView", "template_name"),
        ("django.views.generic", "TemplateView", "extra_context"),
        ("django.views.generic", "RedirectView", "pattern_name"),
        ("django.views.generic", "DetailView", "slug_url_kwarg"),
        ("django.views.generic", "ListView", "paginate_by"),
        ("django.views.generic", "FormView", "form_class"),
        ("django.views.generic", "CreateView", "fields"),
        ("django.views.generic", "DeleteView", "template_name_suffix"),
        ("django.apps", "AppConfig", "name"),
        ("django.apps", "AppConfig", "default_auto_field"),
        ("django.contrib.admin.apps", "SimpleAdminConfig", "default_site"),
        ("django.contrib.admin.apps", "AdminConfig", "default_site"),
        ("django.core.management.base", "BaseCommand", "help"),
        (
            "django.core.management.base",
            "BaseCommand",
            "requires_system_checks",
        ),
        ("django.core.management.base", "LabelCommand", "label"),
        ("django.db.migrations", "Migration", "dependencies"),
        ("django.db.migrations", "Migration", "operations"),
        ("django.contrib.admin", "ModelAdmin", "list_display"),
        ("django.contrib.admin", "ModelAdmin", "actions"),
        ("django.contrib.admin", "TabularInline", "extra"),
        ("django.contrib.admin", "AdminSite", "site_header"),
        ("django.utils.deprecation", "MiddlewareMixin", "async_capable"),
        (
            "django.contrib.auth.backends",
            "RemoteUserBackend",
            "create_unknown_user",
        ),
    ],
)
def test_framework_attributes_are_scoped(module, base, attribute):
    scanner = scan(
        f"""\
        from {module} import {base}

        class Component({base}):
            {attribute} = "value"
            unused_option = "value"

        class Ordinary:
            {attribute} = "value"
        """
    )

    assert definitions(scanner.unused_vars) == {
        ("unused_option", 5),
        (attribute, 8),
    }
    evidence = [
        entry for entry in scanner.django_evidence if entry.name == attribute
    ]
    assert len(evidence) == 1
    assert evidence[0].rule == "django.framework-attribute"


@pytest.mark.parametrize(
    ("base", "hook"),
    [
        ("View", "get_queryset"),
        ("TemplateView", "get_queryset"),
        ("FormView", "get_queryset"),
        ("ListView", "form_valid"),
        ("DetailView", "get_paginate_by"),
        ("View", "get_template_names"),
        ("TemplateView", "get_redirect_url"),
    ],
)
def test_view_hooks_require_the_relevant_protocol(base, hook):
    scanner = scan(
        f"""\
        from django.views.generic import {base}

        class Component({base}):
            def {hook}(self):
                pass
        """
    )
    assert definitions(scanner.unused_methods) == {(hook, 4)}


@pytest.mark.parametrize(
    ("module", "base", "hook"),
    [
        ("django.views.generic.base", "ContextMixin", "get"),
        ("django.core.management.base", "BaseCommand", "handle_app_config"),
        ("django.core.management.base", "BaseCommand", "handle_label"),
        ("django.contrib.admin", "ModelAdmin", "get_extra"),
        ("django.contrib.admin", "TabularInline", "save_model"),
        ("django.contrib.auth.backends", "BaseBackend", "clean_username"),
        ("django.contrib.auth.backends", "ModelBackend", "configure_user"),
    ],
)
def test_subclass_specific_hooks_are_not_generalized(module, base, hook):
    scanner = scan(
        f"""\
        from {module} import {base}

        class Component({base}):
            def {hook}(self):
                pass
        """
    )
    assert definitions(scanner.unused_methods) == {(hook, 4)}


def test_protocol_hooks_and_attributes_preserve_member_types():
    scanner = scan(
        """\
        from django.views.generic import TemplateView

        class Component(TemplateView):
            get = 1

            def template_name(self):
                pass
        """
    )
    assert definitions(scanner.unused_methods) == {("template_name", 6)}
    assert definitions(scanner.unused_vars) == {("get", 4)}


def test_hook_named_argument_is_still_reported():
    scanner = scan(
        """\
        from django.views import View

        class Component(View):
            def get(self, get):
                unused = 1
        """
    )
    assert definitions(scanner.unused_methods) == set()
    assert definitions(scanner.unused_vars) == {("get", 4), ("unused", 5)}


def test_conditional_members_do_not_claim_unconditional_framework_usage():
    scanner = scan(
        """\
        from django.views.generic import TemplateView
        from settings import enabled

        class Component(TemplateView):
            if enabled:
                template_name = "index.html"

                def get(self):
                    pass
        """
    )
    assert definitions(scanner.unused_methods) == {("get", 8)}
    assert definitions(scanner.unused_vars) == {("template_name", 6)}


def test_assignment_named_argument_is_still_reported():
    scanner = scan(
        """\
        from django.views.generic import TemplateView

        class Component(TemplateView):
            template_name = lambda template_name: "index.html"
        """
    )
    assert definitions(scanner.unused_vars) == {("template_name", 4)}


@pytest.mark.parametrize("child_first", [False, True])
def test_cross_module_aliases_and_local_inheritance(tmp_path, child_first):
    scanner = Voldemort(project_root=tmp_path)
    modules = [
        (
            "base.py",
            """\
            from django.views.generic import ListView as Parent

            class Listing(Parent):
                paginate_by = 10
            """,
        ),
        (
            "child.py",
            """\
            from base import Listing as Parent

            class BookList(Parent):
                def get_queryset(self):
                    pass

                def form_valid(self):
                    pass
            """,
        ),
    ]
    if child_first:
        modules.reverse()
    for filename, source in modules:
        scanner.scan(dedent(source), filename=tmp_path / filename)
        scanner.get_unused_code()
    assert {item.name for item in scanner.unused_methods} == {"form_valid"}
    assert "paginate_by" not in {item.name for item in scanner.unused_vars}


@pytest.mark.parametrize("mixin_first", [False, True])
def test_framework_mro_preserves_shadowed_plain_mixin_hooks(mixin_first):
    bases = (
        "PlainMixin, TemplateView"
        if mixin_first
        else "TemplateView, PlainMixin"
    )
    scanner = scan(
        f"""\
        from django.views.generic import TemplateView

        class PlainMixin:
            def get_context_data(self):
                pass

        class Component({bases}):
            pass
        """
    )
    assert definitions(scanner.unused_methods) == (
        set() if mixin_first else {("get_context_data", 4)}
    )


def test_view_without_handler_does_not_shadow_later_plain_mixin():
    scanner = scan(
        """\
        from django.views import View

        class PlainMixin:
            def get(self):
                pass

        class Component(View, PlainMixin):
            pass
        """
    )
    assert definitions(scanner.unused_methods) == set()


def test_known_form_ancestors_do_not_block_view_handler_lookup():
    scanner = scan(
        """\
        from django.forms import Form
        from django.views import View

        class PlainMixin:
            def get(self):
                pass

        class Component(Form, PlainMixin, View):
            pass
        """
    )
    assert definitions(scanner.unused_methods) == set()


def test_framework_base_attribute_shadows_later_plain_mixin():
    scanner = scan(
        """\
        from django.views.generic import ListView

        class PlainMixin:
            paginate_by = 10

        class Component(ListView, PlainMixin):
            pass
        """
    )
    assert definitions(scanner.unused_vars) == {("paginate_by", 4)}


def test_appconfig_dynamic_attribute_does_not_shadow_later_plain_mixin():
    scanner = scan(
        """\
        from django.apps import AppConfig

        class PlainMixin:
            name = "shop"

        class ShopConfig(AppConfig, PlainMixin):
            pass
        """
    )
    assert definitions(scanner.unused_vars) == set()


def test_external_cooperative_super_retains_intermediate_plain_mixin():
    scanner = scan(
        """\
        from django.views.generic.detail import SingleObjectMixin
        from django.views.generic.base import ContextMixin

        class PlainMixin:
            def get_context_data(self):
                pass

        class Component(SingleObjectMixin, PlainMixin, ContextMixin):
            pass
        """
    )
    assert definitions(scanner.unused_methods) == set()


def test_local_cooperative_super_retains_delegated_mixin():
    scanner = scan(
        """\
        from django.views import View

        class PlainMixin:
            def get(self):
                pass

        class Component(PlainMixin, View):
            def get(self):
                return super().get()
        """
    )
    assert definitions(scanner.unused_methods) == set()


def test_local_terminal_override_keeps_shadowed_mixin_reportable():
    scanner = scan(
        """\
        from django.views import View

        class PlainMixin:
            def get(self):
                pass

        class Component(PlainMixin, View):
            def get(self):
                return None
        """
    )
    assert definitions(scanner.unused_methods) == {("get", 4)}


def test_nested_helper_does_not_imply_super_forwarding():
    scanner = scan(
        """\
        from django.views import View

        class PlainMixin:
            def get(self):
                pass

        class Component(PlainMixin, View):
            def get(self):
                def unused_helper(self):
                    return super().get()
                return None
        """
    )
    assert not any(
        entry.qualified_name == "example.PlainMixin.get"
        for entry in scanner.django_evidence
    )


def test_lambda_helper_does_not_imply_super_forwarding():
    scanner = scan(
        """\
        from django.views import View

        class PlainMixin:
            def get(self):
                pass

        class Component(PlainMixin, View):
            def get(self):
                unused_helper = lambda: super().get()
                return None
        """
    )
    assert not any(
        entry.qualified_name == "example.PlainMixin.get"
        for entry in scanner.django_evidence
    )


@pytest.mark.parametrize("base", ["ModelAdmin", "TabularInline"])
def test_admin_media_property_is_scoped(base):
    scanner = scan(
        f"""\
        from django.contrib.admin import {base}

        class Component({base}):
            @property
            def media(self):
                unused = 1

        class Ordinary:
            @property
            def media(self):
                pass
        """
    )
    assert definitions(scanner.unused_props) == {("media", 9)}
    assert definitions(scanner.unused_vars) == {("unused", 6)}


@pytest.mark.parametrize("base", ["ModelAdmin", "TabularInline"])
def test_admin_media_declarations_and_options_are_scoped(base):
    scanner = scan(
        f"""\
        from django.contrib.admin import {base}

        class Component({base}):
            class Media:
                js = ("admin.js",)
                css = {{"all": ("admin.css",)}}
                extend = False
                unused_option = 1

        class Ordinary:
            class Media:
                js = ("admin.js",)
                css = {{"all": ("admin.css",)}}
        """
    )
    assert definitions(scanner.unused_classes) == {
        ("Component", 3),
        ("Ordinary", 10),
        ("Media", 11),
    }
    assert definitions(scanner.unused_vars) == {
        ("unused_option", 8),
        ("js", 12),
        ("css", 13),
    }


@pytest.mark.parametrize(
    ("base", "forwarded"),
    [
        ("StackedInline", True),
        ("TabularInline", True),
        ("ModelAdmin", False),
        ("InlineModelAdmin", False),
    ],
)
def test_admin_media_forwarding_matches_django_mro(base, forwarded):
    bases = f"{base}, PlainMixin"
    if forwarded:
        bases += ", InlineModelAdmin"
    scanner = scan(
        f"""\
        from django.contrib.admin import {base}, InlineModelAdmin

        class PlainMixin:
            @property
            def media(self):
                return None

        class Component({bases}):
            pass
        """
    )
    assert (("media", 4) not in definitions(scanner.unused_props)) == forwarded


def test_explicit_admin_media_property_does_not_consume_media_declaration():
    scanner = scan(
        """\
        from django.contrib.admin import ModelAdmin

        class Component(ModelAdmin):
            class Media:
                js = ("admin.js",)

            @property
            def media(self):
                return None
        """
    )
    assert ("Media", 4) in definitions(scanner.unused_classes)
    assert definitions(scanner.unused_vars) == {("js", 5)}


def test_unknown_external_base_does_not_exempt_inherited_hooks():
    scanner = scan(
        """\
        from external import Unknown
        from django.views import View

        class PlainMixin:
            def get(self):
                pass

        class Component(Unknown, PlainMixin, View):
            pass
        """
    )
    assert definitions(scanner.unused_methods) == {("get", 5)}
    assert any("Unresolved" in note for note in scanner.django_notes)


def test_same_named_non_django_classes_get_no_exemptions():
    scanner = scan(
        """\
        from package import ListView, ModelAdmin

        class Listing(ListView):
            paginate_by = 10

            def get_queryset(self):
                pass

        class Admin(ModelAdmin):
            list_display = ("title",)

            def save_model(self):
                pass
        """
    )
    assert {item.name for item in scanner.unused_methods} == {
        "get_queryset",
        "save_model",
    }
    assert {item.name for item in scanner.unused_vars} == {
        "paginate_by",
        "list_display",
    }


def test_framework_analysis_can_be_disabled():
    scanner = scan(
        """\
        from django.views import View

        class Component(View):
            def get(self):
                pass
        """,
        django_enabled=False,
    )
    assert definitions(scanner.unused_methods) == {("get", 4)}
    assert scanner.django_evidence == []


def test_external_contract_linearization_is_complete():
    assert framework.EXTERNAL_BASES[
        "django.views.generic.edit.CreateView"
    ] == (
        "django.views.generic.detail.SingleObjectTemplateResponseMixin",
        "django.views.generic.base.TemplateResponseMixin",
        "django.views.generic.edit.BaseCreateView",
        "django.views.generic.edit.ModelFormMixin",
        "django.views.generic.edit.FormMixin",
        "django.views.generic.detail.SingleObjectMixin",
        "django.views.generic.base.ContextMixin",
        "django.views.generic.edit.ProcessFormView",
        "django.views.generic.base.View",
        "builtins.object",
    )
    assert "get" not in framework.known_members("django.views.View")
    assert "get" in framework.hooks(framework.kinds("django.views.View"))
    assert framework.kinds("package.ListView") == set()
