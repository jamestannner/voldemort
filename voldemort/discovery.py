"""Discover Django entry points without loading project or Django code.

Only scanned modules and bounded literal expressions contribute references.
Application imports and framework string contracts are followed independently
of Vulture's global collection of used names.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .django import _Class


@dataclass
class Discovery:
    apps: list[str] = field(default_factory=list)
    loaded_modules: set[str] = field(default_factory=set)
    app_configs: list[_Class] = field(default_factory=list)
    admin_enabled: bool = False
    url_modules: set[str] = field(default_factory=set)
    settings_module: str = ""


@dataclass
class _Value:
    known: bool = False
    value: object = None
    origins: set[tuple[str, str]] = field(default_factory=set)


@dataclass(frozen=True)
class _ModuleValue:
    name: str


def _is_class(value):
    return isinstance(getattr(value, "node", None), ast.ClassDef)


def _static_body(nodes):
    """Yield unconditional statements, respecting constant conditionals."""
    for node in nodes:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Constant):
            yield from _static_body(
                node.body if node.test.value else node.orelse
            )
        else:
            yield node


class _Constants:
    """A small expression interpreter; function calls are never evaluated."""

    def __init__(self, index):
        self.index = index
        self.cache = {}
        self.visiting = set()
        self.call_environments = {}
        self.remaining = 20000

    def module(self, name):
        if name in self.cache:
            return self.cache[name]
        if name in self.visiting or name not in self.index.modules:
            return {}
        self.visiting.add(name)
        module = self.index.modules[name]
        env = {}
        for node in _static_body(module.tree.body):
            if (
                isinstance(
                    node, (ast.Expr, ast.Assign, ast.AnnAssign, ast.AugAssign)
                )
                and node.value is not None
            ):
                for call in self.index._calls(node.value):
                    self.call_environments[id(call)] = env.copy()
            if isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".")[0]
                    env[local] = _Value(
                        True,
                        _ModuleValue(alias.name if alias.asname else local),
                        {(name, local)},
                    )
            elif isinstance(node, ast.ImportFrom):
                source = self.index._import_module(node, module)
                imported = self.module(source) if source else {}
                for alias in node.names:
                    if alias.name == "*":
                        # Unknown star imports could replace any prior value.
                        if source not in self.index.modules:
                            env = {
                                key: _Value(origins=value.origins)
                                for key, value in env.items()
                            }
                        else:
                            exports = imported.get("__all__")
                            if exports is None:
                                names = [
                                    key
                                    for key in imported
                                    if not key.startswith("_")
                                ]
                            elif (
                                exports.known
                                and isinstance(exports.value, (list, tuple))
                                and all(
                                    isinstance(key, str)
                                    for key in exports.value
                                )
                            ):
                                names = exports.value
                            else:
                                self.index.note(
                                    "Cannot resolve star import exports "
                                    f"from {source}."
                                )
                                # An unknown export list can replace prior
                                # bindings or omit any potential setting.
                                env = {
                                    key: _Value()
                                    for key in env.keys() | imported.keys()
                                }
                                continue
                            env.update(
                                (key, imported.get(key, _Value()))
                                for key in names
                            )
                    else:
                        local = alias.asname or alias.name
                        value = imported.get(alias.name)
                        if value is None:
                            child = f"{source}.{alias.name}"
                            value = _Value(
                                child in self.index.modules,
                                _ModuleValue(child),
                            )
                        env[local] = _Value(
                            value.known,
                            value.value,
                            value.origins | {(name, local)},
                        )
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = (
                    node.targets
                    if isinstance(node, ast.Assign)
                    else [node.target]
                )
                value = self.expression(node.value, env)
                for target in targets:
                    if isinstance(target, ast.Name):
                        env[target.id] = _Value(
                            value.known,
                            value.value,
                            value.origins | {(name, target.id)},
                        )
                    else:
                        self.invalidate(target, env)
            elif isinstance(node, ast.AugAssign):
                if isinstance(node.target, ast.Name):
                    left = env.get(node.target.id, _Value())
                    right = self.expression(node.value, env)
                    value = self.combine(left, right, node.op)
                    value.origins.add((name, node.target.id))
                    env[node.target.id] = value
                else:
                    self.invalidate(node.target, env)
            elif isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                env[node.name] = _Value(origins={(name, node.name)})
            else:
                self.invalidate(node, env)
        self.visiting.remove(name)
        self.cache[name] = env
        return env

    def invalidate(self, node, env):
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            env[node.name] = _Value()
            return
        if isinstance(node, ast.Name) and isinstance(
            node.ctx, (ast.Store, ast.Del)
        ):
            prior = env.get(node.id, _Value())
            env[node.id] = _Value(origins=prior.origins)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar, ast.ExceptHandler)):
            if node.name:
                prior = env.get(node.name, _Value())
                env[node.name] = _Value(origins=prior.origins)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            prior = env.get(node.rest, _Value())
            env[node.rest] = _Value(origins=prior.origins)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    for key, prior in list(env.items()):
                        env[key] = _Value(origins=prior.origins)
                else:
                    key = alias.asname or alias.name.split(".")[0]
                    env[key] = _Value()
        elif isinstance(node, (ast.Attribute, ast.Subscript)):
            # Assignment or a method call can mutate a literal container.
            root = node
            while isinstance(root, (ast.Attribute, ast.Subscript)):
                root = root.value
            if isinstance(root, ast.Name) and root.id in env:
                prior = env[root.id]
                self.invalidate_aliases(prior.value, env)
                env[root.id] = _Value(origins=prior.origins)
        elif isinstance(node, ast.Call) and isinstance(
            node.func, ast.Attribute
        ):
            target = self.expression(node.func.value, env)
            if target.known:
                self.invalidate_aliases(target.value, env)
        for child in ast.iter_child_nodes(node):
            self.invalidate(child, env)

    def invalidate_aliases(self, changed, env):
        if not isinstance(changed, (list, dict, tuple)):
            return

        def contains(value, seen):
            self.remaining -= 1
            if self.remaining < 0 or value is changed:
                return True
            if id(value) in seen:
                return False
            seen = seen | {id(value)}
            if isinstance(value, dict):
                values = value.values()
            elif isinstance(value, (list, tuple)):
                values = value
            else:
                return False
            return any(contains(child, seen) for child in values)

        for environment in [env, *self.cache.values()]:
            for name, value in list(environment.items()):
                if value.known and contains(value.value, set()):
                    environment[name] = _Value(origins=value.origins)

    def expression(self, node, env):
        self.remaining -= 1
        if self.remaining < 0 or node is None:
            return _Value()
        if isinstance(node, ast.Constant):
            return _Value(True, node.value)
        if isinstance(node, ast.Name):
            return env.get(node.id, _Value())
        if isinstance(node, ast.Attribute):
            value = self.expression(node.value, env)
            if value.known and isinstance(value.value, _ModuleValue):
                attributes = self.module(value.value.name)
                if node.attr in attributes:
                    return attributes[node.attr]
                child = f"{value.value.name}.{node.attr}"
                if (
                    child in self.index.modules
                    and "__getattr__" not in attributes
                ):
                    return _Value(True, _ModuleValue(child))
        if isinstance(node, (ast.List, ast.Tuple)):
            values, origins = [], set()
            for item in node.elts:
                expanded = isinstance(item, ast.Starred)
                value = self.expression(item.value if expanded else item, env)
                origins.update(value.origins)
                if not value.known:
                    return _Value(origins=origins)
                if expanded:
                    if not isinstance(value.value, (list, tuple)):
                        return _Value(origins=origins)
                    values.extend(value.value)
                else:
                    values.append(value.value)
                if len(values) > 10000:
                    return _Value(origins=origins)
            return _Value(
                True,
                tuple(values) if isinstance(node, ast.Tuple) else values,
                origins,
            )
        if isinstance(node, ast.Dict):
            values, origins = {}, set()
            for key_node, value_node in zip(
                node.keys, node.values, strict=True
            ):
                value = self.expression(value_node, env)
                origins.update(value.origins)
                if not value.known:
                    return _Value(origins=origins)
                if key_node is None:
                    if not isinstance(value.value, dict):
                        return _Value(origins=origins)
                    values.update(value.value)
                else:
                    key = self.expression(key_node, env)
                    origins.update(key.origins)
                    if not key.known:
                        return _Value(origins=origins)
                    try:
                        values[key.value] = value.value
                    except TypeError:
                        return _Value(origins=origins)
            return _Value(True, values, origins)
        if isinstance(node, ast.BinOp):
            return self.combine(
                self.expression(node.left, env),
                self.expression(node.right, env),
                node.op,
            )
        if isinstance(node, ast.Subscript):
            value = self.expression(node.value, env)
            key = self.expression(node.slice, env)
            origins = value.origins | key.origins
            if value.known and key.known:
                try:
                    return _Value(True, value.value[key.value], origins)
                except (IndexError, KeyError, TypeError):
                    pass
            return _Value(origins=origins)
        return _Value()

    @staticmethod
    def combine(left, right, operator):
        origins = left.origins | right.origins
        if left.known and right.known:
            if (
                isinstance(operator, ast.Add)
                and type(left.value) is type(right.value)
                and isinstance(left.value, (str, list, tuple))
                and len(left.value) + len(right.value) <= 10000
            ):
                return _Value(True, left.value + right.value, origins)
            if (
                isinstance(operator, ast.BitOr)
                and isinstance(left.value, dict)
                and isinstance(right.value, dict)
            ):
                return _Value(True, left.value | right.value, origins)
        return _Value(origins=origins)


def _class_literal(index, cls, name, default=None):
    member = index.effective_member(cls, name)
    if member is None:
        for base in index.mro(cls):
            if isinstance(base, str) and base in _BUILTIN_CONFIGS:
                if name == "name":
                    return True, _BUILTIN_CONFIGS[base]
                if name == "default" and base.endswith(".AdminConfig"):
                    return True, True
        return True, default
    if member[1].uncertain:
        return False, None
    return index.static_literal(member)


_BUILTIN_CONFIGS = {
    "django.contrib.admin.apps.AdminConfig": "django.contrib.admin",
    "django.contrib.admin.apps.SimpleAdminConfig": "django.contrib.admin",
    "django.contrib.auth.apps.AuthConfig": "django.contrib.auth",
    "django.contrib.contenttypes.apps.ContentTypesConfig": (
        "django.contrib.contenttypes"
    ),
    "django.contrib.sessions.apps.SessionsConfig": "django.contrib.sessions",
    "django.contrib.messages.apps.MessagesConfig": "django.contrib.messages",
    "django.contrib.staticfiles.apps.StaticFilesConfig": (
        "django.contrib.staticfiles"
    ),
    "django.contrib.sites.apps.SitesConfig": "django.contrib.sites",
}


def _select_config(index, entry):
    explicit = index.resolve(entry)
    if _is_class(explicit):
        if "app-config" not in index.kind(explicit):
            index.note(f"Configured Django app {entry} is not an AppConfig.")
            return None, None
        known, package = _class_literal(index, explicit, "name")
        if not known or not isinstance(package, str):
            index.note(f"Cannot resolve Django app package for {entry}.")
            return None, None
        return package, explicit
    if entry in _BUILTIN_CONFIGS:
        return _BUILTIN_CONFIGS[entry], entry
    if (
        entry in index.modules
        or entry.startswith("django.contrib.")
        or any(name.startswith(f"{entry}.") for name in index.modules)
    ):
        configs = []
        module = index.modules.get(f"{entry}.apps")
        if module:
            for reference in module.bindings.values():
                candidate = index.resolve(reference)
                if _is_class(candidate) and "app-config" in index.kind(
                    candidate
                ):
                    known, enabled = _class_literal(
                        index, candidate, "default", True
                    )
                    if not known:
                        index.note(
                            f"Cannot resolve AppConfig.default for "
                            f"{candidate.qualified_name}."
                        )
                        return None, None
                    if enabled:
                        configs.append(candidate)
            if len(configs) > 1:
                configs = [
                    candidate
                    for candidate in configs
                    if _class_literal(index, candidate, "default", False)
                    == (True, True)
                ]
            if len(configs) > 1:
                index.note(
                    f"Multiple default AppConfig classes in {entry}.apps."
                )
                return None, None
            if configs:
                candidate = configs[0]
                known, package = _class_literal(index, candidate, "name")
                if known and isinstance(package, str):
                    return package, candidate
                index.note(f"Cannot resolve Django app package for {entry}.")
                return None, None
        return entry, None
    index.note(
        f"Configured Django app {entry} is outside the scan or unresolved."
    )
    return None, None


def _ready_imports(index, cls):
    ready = index.effective_member(cls, "ready")
    if not ready or not isinstance(
        ready[1].node, (ast.FunctionDef, ast.AsyncFunctionDef)
    ):
        return set()
    module = index.modules[ready[0].module]
    index.activate_function(ready[1].reference)
    roots = set()
    for node in _static_body(ready[1].node.body):
        if isinstance(node, (ast.Return, ast.Raise)):
            break
        if isinstance(node, ast.Import):
            roots.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            source = index._import_module(node, module)
            if source:
                roots.add(source)
                for alias in node.names:
                    reference = index.resolve(f"{source}.{alias.name}")
                    if isinstance(reference, str):
                        if reference in index.modules:
                            roots.add(reference)
                    elif reference is not None:
                        roots.add(reference.module)
        elif isinstance(node, (ast.If, ast.Try, ast.For, ast.While)):
            index.note(
                f"Conditional imports in {cls.qualified_name}.ready "
                "cannot be established statically."
            )
    return roots


def _entry_class(index, name, member, kind, rule, reason):
    module = index.modules.get(name)
    if module is None:
        return False
    cls = index.resolve(module.bindings.get(member))
    if _is_class(cls) and kind in index.kind(cls):
        index.retain_class(cls, rule, reason)
        return True
    index.note(f"Cannot resolve Django {member} entry point in {name}.")
    return False


def _children(index, package):
    prefix = f"{package}."
    return {
        name
        for name, module in index.modules.items()
        if name.startswith(prefix)
        and "." not in name[len(prefix) :]
        and not name[len(prefix) :].startswith(("_", "~"))
        and module.filename.name != "__init__.py"
    }


def _string_reference(index, reference, rule, reason, loaded):
    """Apply import_string's module/member boundary to known scanned code."""
    module, separator, _ = reference.rpartition(".")
    if not separator or module not in index.modules:
        return None
    loaded.update(index.import_closure({module}))
    return index.retain_symbol(reference, rule, reason)


def _url_discovery(index, constants, root, loaded):
    found, pending = set(), [root]
    while pending:
        name = pending.pop()
        if name in found:
            continue
        module = index.modules.get(name)
        if module is None:
            index.note(
                f"Django URL module {name} is outside the scan or unresolved."
            )
            continue
        found.add(name)
        env = constants.module(name)
        for member in (
            "urlpatterns",
            "app_name",
            "handler400",
            "handler403",
            "handler404",
            "handler500",
        ):
            index.retain_module_member(
                name,
                member,
                "django.url-variable",
                "URL configuration consumed by Django's URL resolver.",
            )
            if member.startswith("handler"):
                value = env.get(member, _Value())
                if value.known and isinstance(value.value, str):
                    _string_reference(
                        index,
                        value.value,
                        "django.url-reference",
                        f"Callback selected by {name}.{member}.",
                        loaded,
                    )
        for node, bindings in module.calls:
            if not node.args:
                continue
            target = index.resolve_expression(node.func, bindings)
            if target not in {
                "django.urls.include",
                "django.urls.conf.include",
            }:
                continue
            value = constants.expression(
                node.args[0], constants.call_environments.get(id(node), env)
            )
            if (
                value.known
                and isinstance(value.value, tuple)
                and len(value.value) == 2
            ):
                value = _Value(True, value.value[0])
            if value.known and isinstance(value.value, str):
                pending.append(value.value)
            elif value.known and isinstance(value.value, _ModuleValue):
                pending.append(value.value.name)
            else:
                index.note(
                    "Cannot resolve Django URL include in "
                    f"{module.filename}:{node.lineno}."
                )
    return found


def _retain_protocol(index, target, protocol, setting):
    if not _is_class(target):
        return
    from . import framework

    names = framework.hooks({protocol})
    if protocol == "middleware":
        # process_request/process_response belong to MiddlewareMixin, while
        # the handler itself consumes these hooks on any middleware object.
        names.difference_update({"process_request", "process_response"})
        names.update({"__call__", "__init__"})
    elif protocol in {"asgi", "wsgi"}:
        names.add("__call__")
    for name in sorted(names):
        resolved = index.effective_member(target, name)
        if not resolved:
            continue
        owner, member = resolved
        if isinstance(member.node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            rule = "django.framework-hook"
        elif isinstance(member.node, ast.Name):
            rule = "django.framework-attribute"
        else:
            continue
        index.retain(
            owner,
            member,
            rule,
            f"Configured {protocol} protocol consumed by {setting} "
            f"on {target.qualified_name}.",
        )


def discover(index, apps, settings_module=""):
    """Return active entry modules and retain exact framework entry points."""
    result = Discovery(settings_module=settings_module)
    constants = _Constants(index)
    settings = {}
    if settings_module:
        if settings_module not in index.modules:
            index.note(
                f"Django settings module {settings_module} "
                "is outside the scan."
            )
        else:
            settings = constants.module(settings_module)
            result.loaded_modules.update(
                index.import_closure({settings_module})
            )
            for name, value in settings.items():
                if name.isupper():
                    for source, original in value.origins | {
                        (settings_module, name)
                    }:
                        index.retain_module_member(
                            source,
                            original,
                            "django.settings-variable",
                            f"Uppercase setting copied from {settings_module} "
                            "by Django's settings loader.",
                        )
    entries = list(apps)
    if not entries and settings_module:
        installed = settings.get("INSTALLED_APPS", _Value(True, []))
        if (
            installed.known
            and isinstance(installed.value, (tuple, list))
            and all(isinstance(entry, str) for entry in installed.value)
        ):
            entries = installed.value
        else:
            index.note(
                "Cannot resolve INSTALLED_APPS; configure Django app "
                "packages explicitly."
            )
    selected = []
    for entry in entries:
        package, config = _select_config(index, entry)
        if package is None:
            continue
        if package not in result.apps:
            result.apps.append(package)
        selected.append((package, config))
        roots = {package}
        if _is_class(config):
            result.app_configs.append(config)
            roots.add(config.module)
            roots.update(_ready_imports(index, config))
            index.retain_class(
                config,
                "django.app-config",
                f"AppConfig selected by configured Django app {entry}.",
            )
        elif f"{package}.apps" in index.modules:
            roots.add(f"{package}.apps")
        result.loaded_modules.update(index.import_closure(roots))
        if package == "django.contrib.admin":
            if _is_class(config):
                result.admin_enabled |= (
                    "django.contrib.admin.apps.AdminConfig"
                    in index.mro(config)
                )
                known, site = _class_literal(index, config, "default_site")
                if known and isinstance(site, str):
                    _string_reference(
                        index,
                        site,
                        "django.app-config-reference",
                        "AdminSite selected by "
                        f"{config.qualified_name}.default_site.",
                        result.loaded_modules,
                    )
                elif not known:
                    index.note(
                        "Cannot resolve AdminSite selected by "
                        f"{config.qualified_name}.default_site."
                    )
            else:
                result.admin_enabled |= (
                    config != "django.contrib.admin.apps.SimpleAdminConfig"
                )
    if result.admin_enabled:
        result.loaded_modules.update(
            index.import_closure(
                {f"{package}.admin" for package in result.apps}
            )
        )

    migration_value = settings.get("MIGRATION_MODULES", _Value(True, {}))
    migrations = (
        migration_value.value
        if migration_value.known and isinstance(migration_value.value, dict)
        else None
    )
    if migrations is None:
        index.note(
            "Cannot resolve MIGRATION_MODULES; migration discovery "
            "is uncertain."
        )
    for package, config in selected:
        commands = _children(index, f"{package}.management.commands")
        result.loaded_modules.update(index.import_closure(commands))
        for name in commands:
            _entry_class(
                index,
                name,
                "Command",
                "management-command",
                "django.command",
                "Command class loaded by Django's management command loader.",
            )
        label = package.rsplit(".", 1)[-1]
        if _is_class(config):
            known, configured = _class_literal(index, config, "label", label)
            if not known or not isinstance(configured, str):
                index.note(
                    f"Cannot resolve app label for {config.qualified_name}; "
                    "migrations may be missed."
                )
                continue
            label = configured
        migration_package = (
            migrations.get(label, f"{package}.migrations")
            if migrations is not None
            else None
        )
        if migration_package is None:
            continue
        if not isinstance(migration_package, str):
            index.note(f"Cannot resolve migration module for app {package}.")
            continue
        modules = _children(index, migration_package)
        result.loaded_modules.update(index.import_closure(modules))
        for name in modules:
            _entry_class(
                index,
                name,
                "Migration",
                "migration",
                "django.migration",
                "Migration class loaded by Django's migration loader.",
            )

    references = {
        "MIDDLEWARE": "middleware",
        "AUTHENTICATION_BACKENDS": "auth-backend",
        "WSGI_APPLICATION": "wsgi",
        "ASGI_APPLICATION": "asgi",
    }
    for name, protocol in references.items():
        value = settings.get(name)
        if value is None:
            continue
        values = value.value if value.known else None
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, (list, tuple)) or not all(
            isinstance(ref, str) for ref in values
        ):
            index.note(
                f"Cannot resolve Django setting {settings_module}.{name}."
            )
            continue
        for reference in values:
            target = _string_reference(
                index,
                reference,
                "django.settings-reference",
                f"Selected by Django setting {settings_module}.{name}.",
                result.loaded_modules,
            )
            if target is not None:
                _retain_protocol(
                    index, target, protocol, f"{settings_module}.{name}"
                )
    templates = settings.get("TEMPLATES")
    if (
        templates
        and templates.known
        and isinstance(templates.value, (list, tuple))
    ):
        for template in templates.value:
            if not isinstance(template, dict):
                continue
            options = template.get("OPTIONS", {})
            if not isinstance(options, dict):
                continue
            processors = options.get("context_processors", [])
            if isinstance(processors, (list, tuple)):
                for reference in processors:
                    if isinstance(reference, str):
                        _string_reference(
                            index,
                            reference,
                            "django.settings-reference",
                            "Context processor configured by "
                            f"{settings_module}.TEMPLATES.",
                            result.loaded_modules,
                        )
    elif templates:
        index.note(
            f"Cannot resolve Django setting {settings_module}.TEMPLATES."
        )
    root = settings.get("ROOT_URLCONF")
    if root and root.known and isinstance(root.value, str):
        result.url_modules = _url_discovery(
            index, constants, root.value, result.loaded_modules
        )
        result.loaded_modules.update(index.import_closure(result.url_modules))
    elif root:
        index.note(
            f"Cannot resolve Django setting {settings_module}.ROOT_URLCONF."
        )
    return result
