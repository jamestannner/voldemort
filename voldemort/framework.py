"""Static Django 5.2 class protocols and their external inheritance order.

Contracts describe members consumed by a framework protocol. Actual external
declarations are kept separately: View dispatches to ``get`` without defining
it, so an ordinary mixin after View can still supply that handler.
"""

from __future__ import annotations

import ast

from . import contracts
from .contracts import DJANGO_VERSION


def _names(names):
    return frozenset(names.split())


# Each family contributes only its own protocol. A resolved external class
# receives its ancestors' families through the static C3 linearization below.
_CONTRACTS = {
    "view": (
        _names(
            "as_view setup dispatch http_method_not_allowed options "
            "get post put patch delete head trace"
        ),
        _names("http_method_names"),
    ),
    "context": (
        _names("get_context_data"),
        _names("extra_context"),
    ),
    "template-response": (
        _names("render_to_response get_template_names"),
        _names("template_name template_engine response_class content_type"),
    ),
    "redirect-view": (
        _names("get_redirect_url"),
        _names("permanent url pattern_name query_string"),
    ),
    "single-object": (
        _names(
            "get_object get_queryset get_slug_field get_context_object_name"
        ),
        _names(
            "model queryset slug_field context_object_name slug_url_kwarg "
            "pk_url_kwarg query_pk_and_slug"
        ),
    ),
    "single-object-template": (
        _names("get_template_names"),
        _names("template_name_field template_name_suffix"),
    ),
    "multiple-object": (
        _names(
            "get_queryset get_ordering paginate_queryset get_paginate_by "
            "get_paginator get_paginate_orphans get_allow_empty "
            "get_context_object_name"
        ),
        _names(
            "allow_empty queryset model paginate_by paginate_orphans "
            "context_object_name page_kwarg ordering paginator_class"
        ),
    ),
    "multiple-object-template": (
        _names("get_template_names"),
        _names("template_name_suffix"),
    ),
    "form-mixin": (
        _names(
            "get_initial get_prefix get_form_class get_form get_form_kwargs "
            "get_success_url form_valid form_invalid"
        ),
        _names("initial form_class success_url prefix"),
    ),
    "model-form-mixin": (
        _names("get_form_class get_form_kwargs get_success_url form_valid"),
        _names("fields"),
    ),
    "deletion-mixin": (
        _names("delete post get_success_url"),
        _names("success_url"),
    ),
    "app-config": (
        _names("ready get_model get_models import_models"),
        _names("name label verbose_name path default default_auto_field"),
    ),
    "admin-config": (
        frozenset(),
        _names("default_site"),
    ),
    "management-command": (
        _names(
            "get_version create_parser add_arguments add_base_argument "
            "print_help run_from_argv execute get_check_kwargs check "
            "check_migrations handle"
        ),
        _names(
            "help output_transaction requires_migrations_checks "
            "requires_system_checks base_stealth_options stealth_options "
            "suppressed_base_arguments missing_args_message"
        ),
    ),
    "app-command": (
        _names("handle_app_config"),
        frozenset(),
    ),
    "label-command": (
        _names("handle_label"),
        _names("label"),
    ),
    "migration": (
        _names("mutate_state apply unapply suggest_name"),
        _names("operations dependencies run_before replaces initial atomic"),
    ),
    "base-model-admin": (
        _names(
            "check formfield_for_dbfield formfield_for_choice_field "
            "get_field_queryset formfield_for_foreignkey "
            "formfield_for_manytomany get_autocomplete_fields "
            "get_view_on_site_url get_empty_value_display get_exclude "
            "get_fields get_fieldsets get_inlines get_ordering "
            "get_readonly_fields get_prepopulated_fields get_queryset "
            "get_sortable_by lookup_allowed to_field_allowed "
            "has_add_permission has_change_permission has_delete_permission "
            "has_view_permission has_view_or_change_permission "
            "has_module_permission media"
        ),
        _names(
            "autocomplete_fields raw_id_fields fields exclude fieldsets "
            "form filter_vertical filter_horizontal radio_fields "
            "prepopulated_fields formfield_overrides readonly_fields "
            "ordering sortable_by view_on_site show_full_result_count "
            "empty_value_display checks_class"
        ),
    ),
    "model-admin": (
        _names(
            "get_inline_instances get_urls get_model_perms get_form "
            "get_changelist get_changelist_instance get_object "
            "get_changelist_form get_changelist_formset "
            "get_formsets_with_inlines get_paginator log_addition "
            "log_change log_deletion log_deletions action_checkbox "
            "get_actions get_action_choices get_action get_list_display "
            "get_list_display_links get_list_filter get_list_select_related "
            "get_search_fields get_search_results get_preserved_filters "
            "construct_change_message message_user save_form save_model "
            "delete_model delete_queryset save_formset save_related "
            "render_change_form response_add response_change "
            "response_post_save_add response_post_save_change "
            "response_action response_delete render_delete_form "
            "get_inline_formsets get_changeform_initial_data changeform_view "
            "add_view change_view changelist_view get_deleted_objects "
            "delete_view history_view get_formset_kwargs"
        ),
        _names(
            "list_display list_display_links list_filter list_select_related "
            "list_per_page list_max_show_all list_editable search_fields "
            "search_help_text date_hierarchy save_as save_as_continue "
            "save_on_top preserve_filters show_facets inlines paginator "
            "add_form_template change_form_template change_list_template "
            "delete_confirmation_template "
            "delete_selected_confirmation_template object_history_template "
            "popup_response_template actions action_form actions_on_top "
            "actions_on_bottom actions_selection_counter"
        ),
    ),
    "inline-admin": (
        _names("get_extra get_min_num get_max_num get_formset"),
        _names(
            "model fk_name formset extra min_num max_num template "
            "verbose_name verbose_name_plural can_delete show_change_link "
            "classes"
        ),
    ),
    "admin-site": (
        _names(
            "check has_permission admin_view get_urls each_context "
            "password_change password_change_done i18n_javascript logout "
            "login autocomplete_view catch_all_view get_app_list index "
            "app_index get_log_entries"
        ),
        _names(
            "site_title site_header index_title site_url enable_nav_sidebar "
            "empty_value_display login_form index_template "
            "app_index_template login_template logout_template "
            "password_change_template password_change_done_template "
            "final_catch_all_view"
        ),
    ),
    "middleware": (
        _names(
            "process_request process_view process_template_response "
            "process_response process_exception"
        ),
        _names("sync_capable async_capable"),
    ),
    "auth-backend": (
        _names(
            "authenticate aauthenticate get_user aget_user "
            "get_user_permissions aget_user_permissions get_group_permissions "
            "aget_group_permissions get_all_permissions aget_all_permissions "
            "has_perm ahas_perm has_module_perms ahas_module_perms"
        ),
        frozenset(),
    ),
    "model-auth-backend": (
        _names("user_can_authenticate with_perm"),
        frozenset(),
    ),
    "remote-user-backend": (
        _names("clean_username configure_user aconfigure_user"),
        _names("create_unknown_user"),
    ),
}

# Canonical class identities and direct bases. These records are intentionally
# explicit; similarly named classes from other packages receive no exemptions.
_BASE = "django.views.generic.base."
_DETAIL = "django.views.generic.detail."
_LIST = "django.views.generic.list."
_EDIT = "django.views.generic.edit."
_COMMAND = "django.core.management.base."
_ADMIN = "django.contrib.admin.options."

_BASES = {
    _BASE + "View": (),
    _BASE + "ContextMixin": (),
    _BASE + "TemplateResponseMixin": (),
    _BASE + "TemplateView": (
        _BASE + "TemplateResponseMixin",
        _BASE + "ContextMixin",
        _BASE + "View",
    ),
    _BASE + "RedirectView": (_BASE + "View",),
    _DETAIL + "SingleObjectMixin": (_BASE + "ContextMixin",),
    _DETAIL + "BaseDetailView": (
        _DETAIL + "SingleObjectMixin",
        _BASE + "View",
    ),
    _DETAIL + "SingleObjectTemplateResponseMixin": (
        _BASE + "TemplateResponseMixin",
    ),
    _DETAIL + "DetailView": (
        _DETAIL + "SingleObjectTemplateResponseMixin",
        _DETAIL + "BaseDetailView",
    ),
    _LIST + "MultipleObjectMixin": (_BASE + "ContextMixin",),
    _LIST + "BaseListView": (
        _LIST + "MultipleObjectMixin",
        _BASE + "View",
    ),
    _LIST + "MultipleObjectTemplateResponseMixin": (
        _BASE + "TemplateResponseMixin",
    ),
    _LIST + "ListView": (
        _LIST + "MultipleObjectTemplateResponseMixin",
        _LIST + "BaseListView",
    ),
    _EDIT + "FormMixin": (_BASE + "ContextMixin",),
    _EDIT + "ModelFormMixin": (
        _EDIT + "FormMixin",
        _DETAIL + "SingleObjectMixin",
    ),
    _EDIT + "ProcessFormView": (_BASE + "View",),
    _EDIT + "BaseFormView": (
        _EDIT + "FormMixin",
        _EDIT + "ProcessFormView",
    ),
    _EDIT + "FormView": (
        _BASE + "TemplateResponseMixin",
        _EDIT + "BaseFormView",
    ),
    _EDIT + "BaseCreateView": (
        _EDIT + "ModelFormMixin",
        _EDIT + "ProcessFormView",
    ),
    _EDIT + "CreateView": (
        _DETAIL + "SingleObjectTemplateResponseMixin",
        _EDIT + "BaseCreateView",
    ),
    _EDIT + "BaseUpdateView": (
        _EDIT + "ModelFormMixin",
        _EDIT + "ProcessFormView",
    ),
    _EDIT + "UpdateView": (
        _DETAIL + "SingleObjectTemplateResponseMixin",
        _EDIT + "BaseUpdateView",
    ),
    _EDIT + "DeletionMixin": (),
    _EDIT + "BaseDeleteView": (
        _EDIT + "DeletionMixin",
        _EDIT + "FormMixin",
        _DETAIL + "BaseDetailView",
    ),
    _EDIT + "DeleteView": (
        _DETAIL + "SingleObjectTemplateResponseMixin",
        _EDIT + "BaseDeleteView",
    ),
    "django.apps.config.AppConfig": (),
    "django.contrib.admin.apps.SimpleAdminConfig": (
        "django.apps.config.AppConfig",
    ),
    "django.contrib.admin.apps.AdminConfig": (
        "django.contrib.admin.apps.SimpleAdminConfig",
    ),
    _COMMAND + "BaseCommand": (),
    _COMMAND + "AppCommand": (_COMMAND + "BaseCommand",),
    _COMMAND + "LabelCommand": (_COMMAND + "BaseCommand",),
    "django.db.migrations.migration.Migration": (),
    _ADMIN + "BaseModelAdmin": (),
    _ADMIN + "ModelAdmin": (_ADMIN + "BaseModelAdmin",),
    _ADMIN + "InlineModelAdmin": (_ADMIN + "BaseModelAdmin",),
    _ADMIN + "StackedInline": (_ADMIN + "InlineModelAdmin",),
    _ADMIN + "TabularInline": (_ADMIN + "InlineModelAdmin",),
    "django.contrib.admin.sites.AdminSite": (),
    "django.utils.deprecation.MiddlewareMixin": (),
    "django.contrib.auth.backends.BaseBackend": (),
    "django.contrib.auth.backends.ModelBackend": (
        "django.contrib.auth.backends.BaseBackend",
    ),
    "django.contrib.auth.backends.RemoteUserBackend": (
        "django.contrib.auth.backends.ModelBackend",
    ),
}

_FAMILIES = {
    _BASE + "View": "view",
    _BASE + "ContextMixin": "context",
    _BASE + "TemplateResponseMixin": "template-response",
    _BASE + "RedirectView": "redirect-view",
    _DETAIL + "SingleObjectMixin": "single-object",
    _DETAIL + "SingleObjectTemplateResponseMixin": "single-object-template",
    _LIST + "MultipleObjectMixin": "multiple-object",
    _LIST + "MultipleObjectTemplateResponseMixin": "multiple-object-template",
    _EDIT + "FormMixin": "form-mixin",
    _EDIT + "ModelFormMixin": "model-form-mixin",
    _EDIT + "DeletionMixin": "deletion-mixin",
    "django.apps.config.AppConfig": "app-config",
    "django.contrib.admin.apps.SimpleAdminConfig": "admin-config",
    _COMMAND + "BaseCommand": "management-command",
    _COMMAND + "AppCommand": "app-command",
    _COMMAND + "LabelCommand": "label-command",
    "django.db.migrations.migration.Migration": "migration",
    _ADMIN + "BaseModelAdmin": "base-model-admin",
    _ADMIN + "ModelAdmin": "model-admin",
    _ADMIN + "InlineModelAdmin": "inline-admin",
    "django.contrib.admin.sites.AdminSite": "admin-site",
    "django.utils.deprecation.MiddlewareMixin": "middleware",
    "django.contrib.auth.backends.BaseBackend": "auth-backend",
    "django.contrib.auth.backends.ModelBackend": "model-auth-backend",
    "django.contrib.auth.backends.RemoteUserBackend": "remote-user-backend",
}

CANONICAL_BASES = {
    "django.views.View": _BASE + "View",
    "django.views.generic.View": _BASE + "View",
    "django.views.generic.TemplateView": _BASE + "TemplateView",
    "django.views.generic.RedirectView": _BASE + "RedirectView",
    "django.views.generic.DetailView": _DETAIL + "DetailView",
    "django.views.generic.ListView": _LIST + "ListView",
    "django.views.generic.FormView": _EDIT + "FormView",
    "django.views.generic.CreateView": _EDIT + "CreateView",
    "django.views.generic.UpdateView": _EDIT + "UpdateView",
    "django.views.generic.DeleteView": _EDIT + "DeleteView",
    "django.apps.AppConfig": "django.apps.config.AppConfig",
    "django.db.migrations.Migration": (
        "django.db.migrations.migration.Migration"
    ),
    **{
        f"django.contrib.admin.{name}": _ADMIN + name
        for name in (
            "ModelAdmin",
            "InlineModelAdmin",
            "StackedInline",
            "TabularInline",
        )
    },
    "django.contrib.admin.AdminSite": "django.contrib.admin.sites.AdminSite",
}


def _linearization(identity):
    bases = _BASES.get(identity, ()) or ("builtins.object",)
    sequences = [
        (
            [base, *_linearization(base)]
            if base != "builtins.object"
            else [base]
        )
        for base in bases
    ]
    sequences.append(list(bases))
    result = []
    while sequences:
        sequences = [sequence for sequence in sequences if sequence]
        if not sequences:
            break
        candidate = next(
            sequence[0]
            for sequence in sequences
            if not any(sequence[0] in other[1:] for other in sequences)
        )
        result.append(candidate)
        for sequence in sequences:
            if sequence[0] == candidate:
                sequence.pop(0)
    return tuple(result)


EXTERNAL_BASES = {identity: _linearization(identity) for identity in _BASES}
EXTERNAL_BASES.update(
    (alias, EXTERNAL_BASES[identity])
    for alias, identity in CANONICAL_BASES.items()
)


def kinds(identity: str) -> set[str]:
    """Return the framework protocols of an exact imported class identity."""
    identity = CANONICAL_BASES.get(identity, identity)
    return {
        _FAMILIES[base]
        for base in (identity, *EXTERNAL_BASES.get(identity, ()))
        if base in _FAMILIES
    }


def _protocol_members(families, position):
    return set().union(
        *(
            _CONTRACTS[family][position]
            for family in families
            if family in _CONTRACTS
        )
    )


def hooks(kinds: set[str]) -> set[str]:
    """Names consumed by the supplied protocols, including configuration."""
    return _protocol_members(kinds, 0) | _protocol_members(kinds, 1)


def _forwards_to_super(node, name):
    pending = list(node.body)
    while pending:
        child = pending.pop()
        if isinstance(
            child,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda),
        ):
            continue
        if (
            isinstance(child, ast.Attribute)
            and child.attr == name
            and isinstance(child.value, ast.Call)
            and isinstance(child.value.func, ast.Name)
            and child.value.func.id == "super"
            and not child.value.args
            and not child.value.keywords
        ):
            return True
        pending.extend(ast.iter_child_nodes(child))
    return False


def _hook_members(index, cls, name):
    """Follow effective hooks and explicit cooperative super forwarding."""
    for base in index.mro(cls):
        if isinstance(base, str):
            identity = CANONICAL_BASES.get(base, base)
            if identity in EXTERNAL_MEMBERS:
                if name in known_members(
                    identity
                ) and name not in EXTERNAL_FORWARDING.get(identity, ()):
                    return
            elif identity in contracts.EXTERNAL_MEMBERS:
                if name in contracts.EXTERNAL_MEMBERS[identity]:
                    return
            elif name in index.hooks(index.kind(base)):
                return
            elif not index.kind(base) and base not in {
                "builtins.object",
                "builtins.type",
            }:
                index.note(
                    f"Unresolved inherited member lookup in "
                    f"{cls.qualified_name} through {base}."
                )
                return
        else:
            member = base.members.get(name)
            if member:
                if member.uncertain or not isinstance(
                    member.node, (ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    return
                yield base, member
                if not _forwards_to_super(member.node, name):
                    return


def _retain_media(index, cls):
    # MediaDefiningClass generates a media property only when the class does
    # not override it. An explicit media implementation need not read Media.
    if "media" in cls.members:
        return
    resolved = index.effective_member(cls, "Media")
    if not resolved or resolved[1].uncertain:
        return
    owner, member = resolved
    definition = index.resolve(member.reference)
    if not isinstance(getattr(definition, "node", None), ast.ClassDef):
        return
    reason = (
        f"Django {DJANGO_VERSION} admin media declaration consumed on "
        f"{cls.qualified_name}."
    )
    index.retain(owner, member, "django.media", reason)
    index.retain_class(definition, "django.media", reason)
    for name in ("css", "js", "extend"):
        option = index.effective_member(definition, name)
        if (
            option
            and not option[1].uncertain
            and isinstance(option[1].node, ast.Name)
        ):
            index.retain(
                option[0],
                option[1],
                "django.media-option",
                f"Django {DJANGO_VERSION} admin media option consumed on "
                f"{cls.qualified_name}.",
            )


def run(index) -> None:
    """Attach evidence to effective Django members, never to whole classes."""
    for cls in index.classes:
        families = index.kind(cls).intersection(_CONTRACTS)
        if not families:
            continue
        description = ", ".join(sorted(families))
        for name in sorted(_protocol_members(families, 0)):
            for owner, member in _hook_members(index, cls, name):
                index.retain(
                    owner,
                    member,
                    "django.framework-hook",
                    f"Django {DJANGO_VERSION} {description} protocol hook "
                    f"consumed on {cls.qualified_name}.",
                )
        for name in sorted(_protocol_members(families, 1)):
            resolved = index.effective_member(cls, name)
            if (
                resolved
                and not resolved[1].uncertain
                and isinstance(resolved[1].node, ast.Name)
            ):
                owner, member = resolved
                index.retain(
                    owner,
                    member,
                    "django.framework-attribute",
                    f"Documented Django {DJANGO_VERSION} {description} "
                    f"configuration consumed on {cls.qualified_name}.",
                )
        if "base-model-admin" in families:
            _retain_media(index, cls)


# Actual class dictionaries from Django 5.2, separate from consumed protocols.
EXTERNAL_MEMBERS = {
    "django.views.generic.base.View": _names(
        "as_view dispatch http_method_names http_method_not_allowed options "
        "setup view_is_async"
    ),
    "django.views.generic.base.ContextMixin": _names(
        "extra_context get_context_data"
    ),
    "django.views.generic.base.TemplateResponseMixin": _names(
        "content_type get_template_names render_to_response response_class "
        "template_engine template_name"
    ),
    "django.views.generic.base.TemplateView": _names("get"),
    "django.views.generic.base.RedirectView": _names(
        "delete get get_redirect_url head options patch pattern_name "
        "permanent post put query_string url"
    ),
    "django.views.generic.detail.SingleObjectMixin": _names(
        "context_object_name get_context_data get_context_object_name "
        "get_object get_queryset get_slug_field model pk_url_kwarg "
        "query_pk_and_slug queryset slug_field slug_url_kwarg"
    ),
    "django.views.generic.detail.BaseDetailView": _names("get"),
    "django.views.generic.detail.SingleObjectTemplateResponseMixin": _names(
        "get_template_names template_name_field template_name_suffix"
    ),
    "django.views.generic.detail.DetailView": _names(""),
    "django.views.generic.list.MultipleObjectMixin": _names(
        "allow_empty context_object_name get_allow_empty get_context_data "
        "get_context_object_name get_ordering get_paginate_by "
        "get_paginate_orphans get_paginator get_queryset model ordering "
        "page_kwarg paginate_by paginate_orphans paginate_queryset "
        "paginator_class queryset"
    ),
    "django.views.generic.list.BaseListView": _names("get"),
    "django.views.generic.list.MultipleObjectTemplateResponseMixin": _names(
        "get_template_names template_name_suffix"
    ),
    "django.views.generic.list.ListView": _names(""),
    "django.views.generic.edit.FormMixin": _names(
        "form_class form_invalid form_valid get_context_data get_form "
        "get_form_class get_form_kwargs get_initial get_prefix "
        "get_success_url initial prefix success_url"
    ),
    "django.views.generic.edit.ModelFormMixin": _names(
        "fields form_valid get_form_class get_form_kwargs get_success_url"
    ),
    "django.views.generic.edit.ProcessFormView": _names("get post put"),
    "django.views.generic.edit.BaseFormView": _names(""),
    "django.views.generic.edit.FormView": _names(""),
    "django.views.generic.edit.BaseCreateView": _names("get post"),
    "django.views.generic.edit.CreateView": _names("template_name_suffix"),
    "django.views.generic.edit.BaseUpdateView": _names("get post"),
    "django.views.generic.edit.UpdateView": _names("template_name_suffix"),
    "django.views.generic.edit.DeletionMixin": _names(
        "delete get_success_url post success_url"
    ),
    "django.views.generic.edit.BaseDeleteView": _names(
        "form_class form_valid post"
    ),
    "django.views.generic.edit.DeleteView": _names("template_name_suffix"),
    "django.apps.config.AppConfig": _names(
        "create default_auto_field get_model get_models import_models ready"
    ),
    "django.core.management.base.BaseCommand": _names(
        "add_arguments add_base_argument base_stealth_options check "
        "check_migrations create_parser execute get_check_kwargs get_version "
        "handle help output_transaction print_help "
        "requires_migrations_checks requires_system_checks run_from_argv "
        "stealth_options suppressed_base_arguments"
    ),
    "django.core.management.base.AppCommand": _names(
        "add_arguments handle handle_app_config missing_args_message"
    ),
    "django.core.management.base.LabelCommand": _names(
        "add_arguments handle handle_label label missing_args_message"
    ),
    "django.db.migrations.migration.Migration": _names(
        "apply atomic dependencies initial mutate_state operations replaces "
        "run_before suggest_name unapply"
    ),
    "django.contrib.admin.options.BaseModelAdmin": _names(
        "autocomplete_fields check checks_class exclude fields fieldsets "
        "filter_horizontal filter_vertical form formfield_for_choice_field "
        "formfield_for_dbfield formfield_for_foreignkey "
        "formfield_for_manytomany formfield_overrides "
        "get_autocomplete_fields get_empty_value_display get_exclude "
        "get_field_queryset get_fields get_fieldsets get_inlines "
        "get_ordering get_prepopulated_fields get_queryset "
        "get_readonly_fields get_sortable_by get_view_on_site_url "
        "has_add_permission has_change_permission has_delete_permission "
        "has_module_permission has_view_or_change_permission "
        "has_view_permission lookup_allowed media ordering "
        "prepopulated_fields radio_fields raw_id_fields readonly_fields "
        "show_full_result_count sortable_by to_field_allowed view_on_site"
    ),
    "django.contrib.admin.options.ModelAdmin": _names(
        "action_checkbox action_form actions actions_on_bottom "
        "actions_on_top actions_selection_counter add_form_template add_view "
        "change_form_template change_list_template change_view "
        "changeform_view changelist_view checks_class "
        "construct_change_message date_hierarchy "
        "delete_confirmation_template delete_model delete_queryset "
        "delete_selected_confirmation_template delete_view get_action "
        "get_action_choices get_actions get_changeform_initial_data "
        "get_changelist get_changelist_form get_changelist_formset "
        "get_changelist_instance get_deleted_objects get_form "
        "get_formset_kwargs get_formsets_with_inlines get_inline_formsets "
        "get_inline_instances get_list_display get_list_display_links "
        "get_list_filter get_list_select_related get_model_perms get_object "
        "get_paginator get_preserved_filters get_search_fields "
        "get_search_results get_urls history_view inlines list_display "
        "list_display_links list_editable list_filter list_max_show_all "
        "list_per_page list_select_related log_addition log_change "
        "log_deletion log_deletions media message_user "
        "object_history_template paginator popup_response_template "
        "preserve_filters render_change_form render_delete_form "
        "response_action response_add response_change response_delete "
        "response_post_save_add response_post_save_change save_as "
        "save_as_continue save_form save_formset save_model save_on_top "
        "save_related search_fields search_help_text show_facets urls"
    ),
    "django.contrib.admin.options.InlineModelAdmin": _names(
        "can_delete checks_class classes extra fk_name formset get_extra "
        "get_formset get_max_num get_min_num get_queryset has_add_permission "
        "has_change_permission has_delete_permission has_view_permission "
        "max_num media min_num model show_change_link template verbose_name "
        "verbose_name_plural"
    ),
    "django.contrib.admin.options.StackedInline": _names("media template"),
    "django.contrib.admin.options.TabularInline": _names("media template"),
    "django.contrib.admin.sites.AdminSite": _names(
        "actions add_action admin_view app_index app_index_template "
        "autocomplete_view catch_all_view check disable_action each_context "
        "empty_value_display enable_nav_sidebar final_catch_all_view "
        "get_action get_app_list get_log_entries get_model_admin get_urls "
        "has_permission i18n_javascript index index_template index_title "
        "is_registered login login_form login_template logout "
        "logout_template password_change password_change_done "
        "password_change_done_template password_change_template register "
        "site_header site_title site_url unregister urls"
    ),
    "django.utils.deprecation.MiddlewareMixin": _names(
        "async_capable sync_capable"
    ),
    "django.contrib.auth.backends.BaseBackend": _names(
        "aauthenticate aget_all_permissions aget_group_permissions aget_user "
        "aget_user_permissions ahas_perm authenticate get_all_permissions "
        "get_group_permissions get_user get_user_permissions has_perm"
    ),
    "django.contrib.auth.backends.ModelBackend": _names(
        "aauthenticate aget_group_permissions aget_user "
        "aget_user_permissions ahas_module_perms ahas_perm authenticate "
        "get_all_permissions get_group_permissions get_user "
        "get_user_permissions has_module_perms has_perm "
        "user_can_authenticate with_perm"
    ),
    "django.contrib.auth.backends.RemoteUserBackend": _names(
        "aauthenticate aconfigure_user authenticate clean_username "
        "configure_user create_unknown_user"
    ),
    "django.contrib.admin.apps.SimpleAdminConfig": _names(
        "default_auto_field default_site name verbose_name ready"
    ),
    "django.contrib.admin.apps.AdminConfig": _names("default ready"),
}


def known_members(identity: str) -> set[str]:
    """Members physically declared on this exact external class."""
    identity = CANONICAL_BASES.get(identity, identity)
    return set(EXTERNAL_MEMBERS.get(identity, ()))


# Same-name cooperative super calls in the Django 5.2 implementations.
EXTERNAL_FORWARDING = {
    "django.views.generic.detail.SingleObjectMixin": _names(
        "get_context_data"
    ),
    "django.views.generic.detail.SingleObjectTemplateResponseMixin": _names(
        "get_template_names"
    ),
    "django.views.generic.list.MultipleObjectMixin": _names(
        "get_context_data"
    ),
    "django.views.generic.list.MultipleObjectTemplateResponseMixin": _names(
        "get_template_names"
    ),
    "django.views.generic.edit.FormMixin": _names("get_context_data"),
    "django.views.generic.edit.ModelFormMixin": _names(
        "form_valid get_form_kwargs"
    ),
    "django.views.generic.edit.BaseCreateView": _names("get post"),
    "django.views.generic.edit.BaseUpdateView": _names("get post"),
    "django.contrib.admin.apps.AdminConfig": _names("ready"),
    "django.core.management.base.LabelCommand": _names("__init__"),
    "django.contrib.admin.options.ModelAdmin": _names("__init__"),
    "django.contrib.admin.options.InlineModelAdmin": _names(
        "__init__ get_queryset has_add_permission has_change_permission "
        "has_delete_permission has_view_permission"
    ),
    "django.utils.deprecation.MiddlewareMixin": _names("__init__"),
    "django.contrib.auth.backends.ModelBackend": _names(
        "ahas_perm get_all_permissions has_perm"
    ),
    "django.contrib.admin.options.BaseModelAdmin": _names("media"),
    "django.contrib.admin.options.StackedInline": _names("media"),
    "django.contrib.admin.options.TabularInline": _names("media"),
}
