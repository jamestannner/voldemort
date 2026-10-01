"""Versioned, static Django contracts; this module never imports Django."""

DJANGO_VERSION = "5.2"

MODEL_BASES = {
    "django.db.models.Model",
    "django.db.models.base.Model",
}
FORM_BASES = {
    "django.forms.Form",
    "django.forms.BaseForm",
    "django.forms.forms.Form",
    "django.forms.forms.BaseForm",
}
MODEL_FORM_BASES = {
    "django.forms.ModelForm",
    "django.forms.BaseModelForm",
    "django.forms.models.ModelForm",
    "django.forms.models.BaseModelForm",
}

# These names are consumed by Django's model/form protocols. The names alone
# are not a whitelist: the analyzer must first resolve the owning class.
MODEL_HOOKS = {
    "save",
    "asave",
    "delete",
    "adelete",
    "clean",
    "clean_fields",
    "full_clean",
    "validate_unique",
    "validate_constraints",
    "from_db",
    "refresh_from_db",
    "arefresh_from_db",
    "get_absolute_url",
}
FORM_HOOKS = {
    "clean",
    "full_clean",
    "is_valid",
    "has_changed",
    "order_fields",
    "get_initial_for_field",
    "get_context",
    "render",
}
MODEL_FORM_HOOKS = {"save", "validate_unique"}

MODEL_META_OPTIONS = {
    "abstract",
    "app_label",
    "base_manager_name",
    "db_table",
    "db_table_comment",
    "db_tablespace",
    "default_manager_name",
    "default_related_name",
    "get_latest_by",
    "managed",
    "order_with_respect_to",
    "ordering",
    "permissions",
    "default_permissions",
    "proxy",
    "required_db_features",
    "required_db_vendor",
    "select_on_save",
    "indexes",
    "unique_together",
    "constraints",
    "verbose_name",
    "verbose_name_plural",
}
MODEL_FORM_META_OPTIONS = {
    "model",
    "fields",
    "exclude",
    "widgets",
    "labels",
    "help_texts",
    "error_messages",
    "field_classes",
    "localized_fields",
    "formfield_callback",
}

_MODEL_FIELD_NAMES = {
    "Field",
    "AutoField",
    "BigAutoField",
    "BigIntegerField",
    "BinaryField",
    "BooleanField",
    "CharField",
    "CommaSeparatedIntegerField",
    "DateField",
    "DateTimeField",
    "DecimalField",
    "DurationField",
    "EmailField",
    "FilePathField",
    "FloatField",
    "GenericIPAddressField",
    "IntegerField",
    "IPAddressField",
    "NullBooleanField",
    "PositiveBigIntegerField",
    "PositiveIntegerField",
    "PositiveSmallIntegerField",
    "SlugField",
    "SmallAutoField",
    "SmallIntegerField",
    "TextField",
    "TimeField",
    "URLField",
    "UUIDField",
}
MODEL_FIELDS = {
    f"{module}.{name}"
    for module in ("django.db.models", "django.db.models.fields")
    for name in _MODEL_FIELD_NAMES
}
for _module, _names in {
    "related": {"ForeignKey", "OneToOneField", "ManyToManyField"},
    "files": {"FileField", "ImageField"},
    "json": {"JSONField"},
    "generated": {"GeneratedField"},
    "composite": {"CompositePrimaryKey"},
}.items():
    MODEL_FIELDS.update(
        f"{module}.{name}"
        for module in (
            "django.db.models",
            f"django.db.models.fields.{_module}",
        )
        for name in _names
    )
MODEL_FIELDS.update(
    {
        "django.contrib.contenttypes.fields.GenericForeignKey",
        "django.contrib.contenttypes.fields.GenericRelation",
    }
)

_FORM_FIELD_NAMES = {
    "Field",
    "BooleanField",
    "CharField",
    "ChoiceField",
    "ComboField",
    "DateField",
    "DateTimeField",
    "DecimalField",
    "DurationField",
    "EmailField",
    "FileField",
    "FilePathField",
    "FloatField",
    "GenericIPAddressField",
    "ImageField",
    "IntegerField",
    "JSONField",
    "MultipleChoiceField",
    "MultiValueField",
    "NullBooleanField",
    "RegexField",
    "SlugField",
    "SplitDateTimeField",
    "TimeField",
    "TypedChoiceField",
    "TypedMultipleChoiceField",
    "URLField",
    "UUIDField",
}
FORM_FIELDS = {
    f"{module}.{name}"
    for module in ("django.forms", "django.forms.fields")
    for name in _FORM_FIELD_NAMES
}
FORM_FIELDS.update(
    f"{module}.{name}"
    for module in ("django.forms", "django.forms.models")
    for name in ("ModelChoiceField", "ModelMultipleChoiceField")
)
MODEL_MANAGERS = {
    "django.db.models.Manager",
    "django.db.models.manager.Manager",
    "django.db.models.manager.BaseManager",
}

# These declarations exist in model metadata but cannot yield ModelForm
# fields. BinaryField instead defaults to editable=False and can opt in.
MODEL_FORM_EXCLUDED_FIELDS = {
    f"{module}.{name}"
    for module in ("django.db.models", "django.db.models.fields")
    for name in ("AutoField", "BigAutoField", "SmallAutoField")
} | {
    "django.db.models.GeneratedField",
    "django.db.models.fields.generated.GeneratedField",
    "django.db.models.CompositePrimaryKey",
    "django.db.models.fields.composite.CompositePrimaryKey",
    "django.contrib.contenttypes.fields.GenericForeignKey",
    "django.contrib.contenttypes.fields.GenericRelation",
}
MODEL_FIELDS_DEFAULT_NONEDITABLE = {
    "django.db.models.BinaryField",
    "django.db.models.fields.BinaryField",
}

# Canonical identities also give the analyzer enough external inheritance to
# respect method resolution order when ordinary mixins follow Django bases.
CANONICAL_BASES = {
    "django.db.models.base.Model": "django.db.models.Model",
    "django.forms.forms.Form": "django.forms.Form",
    "django.forms.forms.BaseForm": "django.forms.BaseForm",
    "django.forms.models.ModelForm": "django.forms.ModelForm",
    "django.forms.models.BaseModelForm": "django.forms.BaseModelForm",
}
EXTERNAL_BASES = {
    "django.db.models.Model": (
        "django.db.models.utils.AltersData",
        "builtins.object",
    ),
    "django.db.models.utils.AltersData": ("builtins.object",),
    "django.forms.BaseForm": (
        "django.forms.utils.RenderableFormMixin",
        "django.forms.utils.RenderableMixin",
        "builtins.object",
    ),
    "django.forms.BaseModelForm": (
        "django.forms.BaseForm",
        "django.forms.utils.RenderableFormMixin",
        "django.forms.utils.RenderableMixin",
        "django.db.models.utils.AltersData",
        "builtins.object",
    ),
    "django.forms.Form": (
        "django.forms.BaseForm",
        "django.forms.utils.RenderableFormMixin",
        "django.forms.utils.RenderableMixin",
        "builtins.object",
    ),
    "django.forms.ModelForm": (
        "django.forms.BaseModelForm",
        "django.forms.BaseForm",
        "django.forms.utils.RenderableFormMixin",
        "django.forms.utils.RenderableMixin",
        "django.db.models.utils.AltersData",
        "builtins.object",
    ),
    "django.forms.utils.RenderableFormMixin": (
        "django.forms.utils.RenderableMixin",
        "builtins.object",
    ),
    "django.forms.utils.RenderableMixin": ("builtins.object",),
}

# Physical declarations are separate from names consumed by a protocol.
EXTERNAL_MEMBERS = {
    "django.db.models.Model": frozenset(
        (
            "__doc__",
            "__eq__",
            "__getstate__",
            "__hash__",
            "__init__",
            "__module__",
            "__reduce__",
            "__repr__",
            "__setstate__",
            "__str__",
            "_check_column_name_clashes",
            "_check_composite_pk",
            "_check_constraints",
            "_check_db_table_comment",
            "_check_default_pk",
            "_check_field_name_clashes",
            "_check_fields",
            "_check_id_field",
            "_check_indexes",
            "_check_local_fields",
            "_check_long_column_names",
            "_check_m2m_through_same_relationship",
            "_check_managers",
            "_check_model",
            "_check_model_name_db_lookup_clashes",
            "_check_ordering",
            "_check_property_name_related_field_accessor_clashes",
            "_check_single_primary_key",
            "_check_swappable",
            "_check_unique_together",
            "_do_insert",
            "_do_update",
            "_get_FIELD_display",
            "_get_expr_references",
            "_get_field_expression_map",
            "_get_next_or_previous_by_FIELD",
            "_get_next_or_previous_in_order",
            "_get_pk_val",
            "_get_unique_checks",
            "_is_pk_set",
            "_parse_save_params",
            "_perform_date_checks",
            "_perform_unique_checks",
            "_prepare_related_fields_for_save",
            "_save_parents",
            "_save_table",
            "_set_pk_val",
            "_validate_force_insert",
            "adelete",
            "arefresh_from_db",
            "asave",
            "check",
            "clean",
            "clean_fields",
            "date_error_message",
            "delete",
            "from_db",
            "full_clean",
            "get_constraints",
            "get_deferred_fields",
            "pk",
            "prepare_database_save",
            "refresh_from_db",
            "save",
            "save_base",
            "serializable_value",
            "unique_error_message",
            "validate_constraints",
            "validate_unique",
        )
    ),
    "django.db.models.utils.AltersData": frozenset(
        (
            "__dict__",
            "__doc__",
            "__init_subclass__",
            "__module__",
            "__weakref__",
        )
    ),
    "django.forms.BaseForm": frozenset(
        (
            "__doc__",
            "__getitem__",
            "__init__",
            "__iter__",
            "__module__",
            "__repr__",
            "_bound_items",
            "_clean_fields",
            "_clean_form",
            "_post_clean",
            "_widget_data_value",
            "add_error",
            "add_initial_prefix",
            "add_prefix",
            "bound_field_class",
            "changed_data",
            "clean",
            "default_renderer",
            "errors",
            "field_order",
            "full_clean",
            "get_context",
            "get_initial_for_field",
            "has_changed",
            "has_error",
            "hidden_fields",
            "is_multipart",
            "is_valid",
            "media",
            "non_field_errors",
            "order_fields",
            "prefix",
            "template_name",
            "template_name_div",
            "template_name_label",
            "template_name_p",
            "template_name_table",
            "template_name_ul",
            "use_required_attribute",
            "visible_fields",
        )
    ),
    "django.forms.BaseModelForm": frozenset(
        (
            "__doc__",
            "__init__",
            "__module__",
            "_get_validation_exclusions",
            "_post_clean",
            "_save_m2m",
            "_update_errors",
            "clean",
            "save",
            "validate_unique",
        )
    ),
    "django.forms.Form": frozenset(
        ("__doc__", "__module__", "base_fields", "declared_fields", "media")
    ),
    "django.forms.ModelForm": frozenset(
        ("__doc__", "__module__", "base_fields", "declared_fields", "media")
    ),
    "django.forms.utils.RenderableFormMixin": frozenset(
        ("__doc__", "__module__", "as_div", "as_p", "as_table", "as_ul")
    ),
    "django.forms.utils.RenderableMixin": frozenset(
        (
            "__dict__",
            "__doc__",
            "__html__",
            "__module__",
            "__str__",
            "__weakref__",
            "get_context",
            "render",
        )
    ),
}
