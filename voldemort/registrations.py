"""Definition-specific evidence for Django registrations and admin lookups.

Registration decorators are executable declarations, not name whitelists.
Admin callbacks are followed only from registrations in statically loaded
modules. Metadata decorators such as ``display`` and ``action`` do not by
themselves establish a callback's use.
"""

from __future__ import annotations

import ast

from .django import _Member

_RECEIVER_DECORATORS = {
    "django.dispatch.receiver",
    "django.dispatch.dispatcher.receiver",
}
_SIGNAL_CLASSES = {
    "django.dispatch.Signal",
    "django.dispatch.dispatcher.Signal",
    "django.db.models.signals.ModelSignal",
}
_SIGNALS = (
    {
        f"django.db.models.signals.{name}"
        for name in (
            "class_prepared",
            "pre_init",
            "post_init",
            "pre_save",
            "post_save",
            "pre_delete",
            "post_delete",
            "m2m_changed",
            "pre_migrate",
            "post_migrate",
        )
    }
    | {
        f"django.core.signals.{name}"
        for name in (
            "request_started",
            "request_finished",
            "got_request_exception",
            "setting_changed",
        )
    }
    | {
        f"django.contrib.auth.signals.{name}"
        for name in ("user_logged_in", "user_logged_out", "user_login_failed")
    }
    | {"django.test.signals.setting_changed"}
)
_ADMIN_DECORATORS = {
    "django.contrib.admin.register",
    "django.contrib.admin.decorators.register",
}
_DEFAULT_SITES = {
    "django.contrib.admin.site",
    "django.contrib.admin.sites.site",
}
_SITE_CLASSES = {
    "django.contrib.admin.AdminSite",
    "django.contrib.admin.sites.AdminSite",
}


def _is_class(value):
    return isinstance(getattr(value, "node", None), ast.ClassDef)


def _is_function(value):
    return isinstance(
        getattr(value, "node", None), (ast.FunctionDef, ast.AsyncFunctionDef)
    )


class _Registrations:
    def __init__(self, index, discovery):
        self.index = index
        self.discovery = discovery
        self.active_admins = []
        self.actions = {}
        self.registered_modules = set()
        self.function_calls = {
            id(node)
            for function in index.functions
            for statement in function.node.body
            for node in ast.walk(statement)
            if isinstance(node, ast.Call)
        }

    def resolve(self, expression, bindings):
        return self.index.resolve(
            self.index.resolve_expression(expression, bindings)
        )

    def note(self, owner, text):
        self.index.note(f"{text} for {owner.qualified_name}.")

    def constructor_kind(self, value, classes, kind=None):
        value = self.index.resolve(value)
        if isinstance(value, str):
            return value in classes
        if _is_class(value):
            if kind and kind in self.index.kind(value):
                return True
            return any(
                isinstance(base, str) and base in classes
                for base in self.index.mro(value)
            )
        return False

    def instance(self, expression, bindings, classes, kind=None):
        """Recognize an instance without interpreting arbitrary factories."""
        value = self.resolve(expression, bindings)
        constructor = getattr(value, "constructor", None)
        if constructor is not None:
            return self.constructor_kind(constructor, classes, kind)
        if isinstance(expression, ast.Call):
            return self.constructor_kind(
                self.resolve(expression.func, bindings), classes, kind
            )
        return False

    def signal(self, expression, bindings, module):
        value = self.resolve(expression, bindings)
        return (isinstance(value, str) and value in _SIGNALS) or self.instance(
            expression, bindings, _SIGNAL_CLASSES
        )

    def signals(self, expression, bindings, module):
        if isinstance(expression, (ast.List, ast.Tuple)):
            return bool(expression.elts) and all(
                self.signal(item, bindings, module) for item in expression.elts
            )
        return self.signal(expression, bindings, module)

    def site(self, expression, bindings, module):
        if expression is None or (
            isinstance(expression, ast.Constant) and expression.value is None
        ):
            return "django.contrib.admin.site"
        value = self.resolve(expression, bindings)
        if isinstance(value, str) and value in _DEFAULT_SITES:
            return "django.contrib.admin.site"
        if self.instance(expression, bindings, _SITE_CLASSES, "admin-site"):
            # A resolved instance gives aliases one common identity. Otherwise
            # use its module/name reference without executing construction.
            return (
                value
                if value is not None
                else (
                    module.name,
                    ast.dump(expression, include_attributes=False),
                )
            )
        return None

    def retain_receiver(self, callback, reason, registration_module=None):
        if _is_function(callback):
            if registration_module is not None:
                self.registered_modules.add(registration_module)
            self.index.retain_symbol(
                callback, "django.signal-receiver", reason
            )

    def decorators(self):
        for module in self.index.modules.values():
            functions = [
                function
                for function in self.index.functions
                if function.module == module.name
            ]
            for function in functions:
                if not _is_function(function):
                    continue
                for decorator in function.node.decorator_list:
                    if not isinstance(decorator, ast.Call):
                        continue
                    if (
                        self.resolve(decorator.func, function.bindings)
                        not in _RECEIVER_DECORATORS
                    ):
                        continue
                    signal = self.argument(decorator, 0, "signal")
                    if signal is not None and self.signals(
                        signal, function.bindings, module
                    ):
                        self.retain_receiver(
                            function,
                            "Receiver registered by Django's receiver "
                            "decorator when its module executes.",
                            module.name,
                        )
                    else:
                        self.note(
                            function,
                            "Cannot resolve Django receiver signal",
                        )
            if module.name not in self.discovery.loaded_modules:
                continue
            for cls in self.index.classes:
                if cls.module != module.name:
                    continue
                for decorator in cls.node.decorator_list:
                    if (
                        isinstance(decorator, ast.Call)
                        and self.resolve(decorator.func, cls.bindings)
                        in _ADMIN_DECORATORS
                    ):
                        self.register_decorator(
                            decorator, cls, cls.bindings, module
                        )

    @staticmethod
    def argument(call, position, name):
        if len(call.args) > position:
            return call.args[position]
        return next(
            (value.value for value in call.keywords if value.arg == name),
            None,
        )

    def models(self, expression, bindings):
        expressions = (
            expression.elts
            if isinstance(expression, (ast.List, ast.Tuple))
            else [expression]
        )
        models = [self.resolve(item, bindings) for item in expressions]
        if not models or not all(
            _is_class(model) and "model" in self.index.kind(model)
            for model in models
        ):
            return []
        return models

    def activate(self, admin, models, site, module, module_execution=True):
        if not _is_class(admin) or "model-admin" not in self.index.kind(admin):
            return
        for model in models:
            if module_execution:
                self.registered_modules.add(module.name)
            self.index.retain_symbol(
                admin,
                "django.admin-registration",
                f"ModelAdmin registered for {model.qualified_name} in a "
                "statically loaded module.",
            )
            self.active_admins.append((admin, model, site))

    def register_decorator(self, call, admin, bindings, module):
        if not _is_class(admin) or "model-admin" not in self.index.kind(admin):
            return
        site_expression = next(
            (item.value for item in call.keywords if item.arg == "site"),
            None,
        )
        site = self.site(site_expression, bindings, module)
        if site is None or not call.args:
            return
        models = []
        for argument in call.args:
            value = self.models(argument, bindings)
            if not value:
                self.note(admin, "Cannot resolve admin registration models")
                return
            models.extend(value)
        self.activate(admin, models, site, module)

    def calls(self):
        for module in self.index.modules.values():
            for call, bindings in module.calls:
                module_execution = id(call) not in self.function_calls
                if isinstance(call.func, ast.Attribute):
                    if call.func.attr == "connect" and self.signal(
                        call.func.value, bindings, module
                    ):
                        receiver = self.argument(call, 0, "receiver")
                        if receiver is not None:
                            self.retain_receiver(
                                self.resolve(receiver, bindings),
                                "Receiver passed to a statically resolved "
                                "Django signal's connect method.",
                                module.name if module_execution else None,
                            )
                    if module.name not in self.discovery.loaded_modules:
                        continue
                    site = self.site(call.func.value, bindings, module)
                    if site is None:
                        continue
                    if call.func.attr == "register":
                        model = self.argument(call, 0, "model_or_iterable")
                        admin = self.argument(call, 1, "admin_class")
                        models = (
                            self.models(model, bindings)
                            if model is not None
                            else []
                        )
                        if models and module_execution:
                            self.registered_modules.add(module.name)
                        if models and admin is not None:
                            self.activate(
                                self.resolve(admin, bindings),
                                models,
                                site,
                                module,
                                module_execution,
                            )
                    elif call.func.attr == "add_action":
                        action = self.argument(call, 0, "action")
                        function = (
                            self.resolve(action, bindings)
                            if action is not None
                            else None
                        )
                        if _is_function(function):
                            name = self.argument(call, 1, "name")
                            if isinstance(name, ast.Constant) and isinstance(
                                name.value, str
                            ):
                                name = name.value
                            elif name is None:
                                name = function.node.name
                            else:
                                continue
                            self.actions[site, name] = function
                            if module_execution:
                                self.registered_modules.add(module.name)
                            self.index.retain_symbol(
                                function,
                                "django.admin-reference",
                                "Action registered on a statically resolved "
                                "Django admin site.",
                            )
                elif (
                    module.name in self.discovery.loaded_modules
                    and isinstance(call.func, ast.Call)
                    and self.resolve(call.func.func, bindings)
                    in _ADMIN_DECORATORS
                    and len(call.args) == 1
                ):
                    self.register_decorator(
                        call.func,
                        self.resolve(call.args[0], bindings),
                        bindings,
                        module,
                    )

    def retain_lookup(self, cls, name, reason):
        member = self.index.effective_member(cls, name)
        if member:
            self.index.retain(*member, "django.admin-reference", reason)
        return member

    def values(self, cls, name):
        member = self.index.effective_member(cls, name)
        if not member:
            return []
        value = member[1].value
        if isinstance(value, (ast.List, ast.Tuple)):
            return [(item, member[1].bindings) for item in value.elts]
        if not (isinstance(value, ast.Constant) and value.value is None):
            self.note(cls, f"Cannot resolve dynamic admin {name}")
        return []

    def field_lookup(self, model, name, reason):
        member = self.index.declared_fields(model, "model").get(name)
        if member:
            self.index.retain(*member, "django.admin-reference", reason)
        return member

    def display_lookup(self, admin, model, name, option):
        reason = (
            f"Referenced by {admin.qualified_name}.{option} for "
            f"{model.qualified_name}."
        )
        # Django's lookup_field checks actual fields before admin callbacks.
        if self.field_lookup(model, name, reason):
            return
        if name != "__str__" and self.retain_lookup(admin, name, reason):
            return
        self.retain_lookup(model, name, reason)

    def callback_options(self, admin, model, site):
        for option in ("list_display", "readonly_fields", "actions"):
            for expression, bindings in self.values(admin, option):
                if isinstance(expression, ast.Constant) and isinstance(
                    expression.value, str
                ):
                    name = expression.value
                    if option == "actions":
                        reason = (
                            f"Action named by {admin.qualified_name}.actions."
                        )
                        if not self.retain_lookup(admin, name, reason):
                            action = self.actions.get((site, name))
                            if action is not None:
                                self.index.retain_symbol(
                                    action, "django.admin-reference", reason
                                )
                    else:
                        self.display_lookup(admin, model, name, option)
                else:
                    callback = self.resolve(expression, bindings)
                    if _is_function(callback):
                        self.index.retain_symbol(
                            callback,
                            "django.admin-reference",
                            f"Callable referenced by "
                            f"{admin.qualified_name}.{option}.",
                        )
                    else:
                        self.note(
                            admin,
                            f"Cannot resolve dynamic admin {option} item",
                        )
        prepopulated = self.index.effective_member(
            admin, "prepopulated_fields"
        )
        if prepopulated:
            known, fields = self.index.static_literal(prepopulated)
            if known and isinstance(fields, dict):
                for name, sources in fields.items():
                    if isinstance(name, str):
                        self.field_lookup(
                            model,
                            name,
                            f"Field configured by {admin.qualified_name}."
                            "prepopulated_fields.",
                        )
                    if isinstance(sources, (list, tuple)):
                        for source in sources:
                            if isinstance(source, str):
                                self.field_lookup(
                                    model,
                                    source,
                                    f"Field configured by "
                                    f"{admin.qualified_name}."
                                    "prepopulated_fields.",
                                )
            else:
                self.note(admin, "Cannot resolve dynamic prepopulated_fields")
        for expression, bindings in self.values(admin, "inlines"):
            inline = self.resolve(expression, bindings)
            if not _is_class(inline) or "inline-admin" not in self.index.kind(
                inline
            ):
                continue
            self.index.retain_symbol(
                inline,
                "django.admin-reference",
                f"Inline configured by {admin.qualified_name}.inlines.",
            )
            inline_model = self.index.effective_member(inline, "model")
            inline_model = (
                self.index.resolve(inline_model[1].reference)
                if inline_model
                else None
            )
            if _is_class(inline_model) and "model" in self.index.kind(
                inline_model
            ):
                for expression, _bindings in self.values(
                    inline, "readonly_fields"
                ):
                    if isinstance(expression, ast.Constant) and isinstance(
                        expression.value, str
                    ):
                        self.display_lookup(
                            inline,
                            inline_model,
                            expression.value,
                            "readonly_fields",
                        )

    def run(self):
        self.decorators()
        self.calls()
        for admin, model, site in self.active_admins:
            self.callback_options(admin, model, site)
        self.registration_imports()

    def registration_imports(self):
        def has_registration(module):
            return bool(
                self.index.import_closure({module}) & self.registered_modules
            )

        for module in self.index.modules.values():
            if module.name not in self.discovery.loaded_modules:
                continue
            for member in module.members.values():
                if member.uncertain:
                    continue
                source = None
                if isinstance(member.node, ast.Import):
                    source = next(
                        (
                            alias.name
                            for alias in member.node.names
                            if (alias.asname or alias.name.split(".")[0])
                            == member.name
                        ),
                        None,
                    )
                elif isinstance(member.node, ast.ImportFrom):
                    reference = self.index.resolve(member.reference)
                    if isinstance(reference, str):
                        source = reference
                if source in self.index.modules and has_registration(source):
                    self.index.retain_module_member(
                        module.name,
                        member.name,
                        "django.registration-import",
                        f"Import executes Django registrations through "
                        f"{source}.",
                    )

        # Django invokes ready on selected configurations. Only unconditional
        # imports whose modules perform known registrations get evidence.
        for cls in getattr(self.discovery, "app_configs", []):
            ready = self.index.effective_member(cls, "ready")
            function = (
                self.index.resolve(ready[1].reference) if ready else None
            )
            if not _is_function(function):
                continue
            module = self.index.modules[function.module]
            self.ready_imports(
                function.node.body, function, module, has_registration
            )

    def ready_imports(self, statements, function, module, has_registration):
        for node in statements:
            if isinstance(node, (ast.Return, ast.Raise)):
                return True
            if isinstance(node, ast.If) and isinstance(
                node.test, ast.Constant
            ):
                if self.ready_imports(
                    node.body if node.test.value else node.orelse,
                    function,
                    module,
                    has_registration,
                ):
                    return True
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                prefix = (
                    self.index._import_module(node, module)
                    if isinstance(node, ast.ImportFrom)
                    else None
                )
                for alias in node.names:
                    source = (
                        f"{prefix}.{alias.name}"
                        if prefix is not None
                        else alias.name
                    )
                    if (
                        source not in self.index.modules
                        or not has_registration(source)
                    ):
                        continue
                    name = alias.asname or (
                        alias.name
                        if prefix is not None
                        else alias.name.split(".")[0]
                    )
                    self.index.retain(
                        function,
                        _Member(name, node),
                        "django.registration-import",
                        f"Selected AppConfig.ready imports registrations "
                        f"through {source}.",
                    )
        return False


def run(index, discovery):
    """Add registration evidence to an already built Django symbol index."""
    _Registrations(index, discovery).run()
