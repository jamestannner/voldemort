"""Bounded, opt-in Django template references without loading Django.

Library callbacks are selected by actual tag/filter registration and template
load statements. Model member references require a concrete typed render
context; an untyped template variable never becomes a global name exemption.
"""

from __future__ import annotations

import ast
import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import contracts, framework
from .discovery import _Constants
from .django import _Class, _Function, _Instance

_LIBRARIES = {"django.template.Library", "django.template.library.Library"}
_REGISTRATIONS = {
    "filter",
    "tag",
    "simple_tag",
    "inclusion_tag",
    "simple_block_tag",
}
_TOKEN = re.compile(r"({{.*?}}|{%.*?%}|{#.*?#})", re.DOTALL)
_WORDS = re.compile(r"""(?:[^\s'\"]|'[^']*'|"[^"]*")+""")
_PATH = re.compile(r"^[a-zA-Z_][a-zA-Z_0-9]*(?:\.[a-zA-Z_0-9]+)*$")
_UNKNOWN = object()


@dataclass(frozen=True)
class _Type:
    cls: _Class
    many: bool = False


@dataclass
class _Tag:
    function: _Function
    kind: str


@dataclass
class _Block:
    source: str
    tokens: list


@dataclass
class _Library:
    module: str
    tags: dict[str, _Tag] = field(default_factory=dict)
    filters: dict[str, _Function] = field(default_factory=dict)


def _literal(node):
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return _UNKNOWN


def _quoted(text):
    if len(text) > 1 and text[0] in "\"'" and text[-1] == text[0]:
        return text[1:-1]
    return None


def _split(text, separator):
    """Split filter syntax while keeping quoted filter arguments intact."""
    parts, start, quote = [], 0, None
    escaped = False
    for position, character in enumerate(text):
        if escaped:
            escaped = False
        elif character == "\\" and quote:
            escaped = True
        elif character == quote:
            quote = None
        elif character in "\"'" and quote is None:
            quote = character
        elif character == separator and quote is None:
            parts.append(text[start:position])
            start = position + 1
    parts.append(text[start:])
    return parts


def _tokens(text):
    blocked = None
    for match in _TOKEN.finditer(text):
        token = match.group()
        if token.startswith("{#"):
            continue
        content = token[2:-2].strip()
        bits = _WORDS.findall(content)
        if blocked:
            if token.startswith("{%") and bits == blocked:
                blocked = None
            continue
        if token.startswith("{%") and bits:
            if bits[0] == "comment":
                blocked = ["endcomment"]
                continue
            if bits[0] == "verbatim":
                blocked = ["endverbatim", *bits[1:]]
                continue
        yield token.startswith("{%"), content, bits


def _scope_nodes(nodes):
    for node in nodes:
        yield node
        if not isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef,
                ast.Lambda,
                ast.ListComp,
                ast.SetComp,
                ast.DictComp,
                ast.GeneratorExp,
            ),
        ):
            yield from _scope_nodes(ast.iter_child_nodes(node))


def _terminates(nodes):
    for node in nodes:
        if isinstance(node, (ast.Return, ast.Raise)):
            return True
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Constant)
            and _terminates(node.body if node.test.value else node.orelse)
        ):
            return True
    return False


def _blocks(source, tokens):
    result, stack = {}, []
    for position, (block, _, bits) in enumerate(tokens):
        if block and len(bits) > 1 and bits[0] == "block":
            stack.append((bits[1], position))
        elif block and bits and bits[0] == "endblock" and stack:
            name, start = stack.pop()
            loads = [
                token
                for token in tokens[:start]
                if token[0] and token[2] and token[2][0] == "load"
            ]
            result[name] = _Block(
                source, [*loads, *tokens[start + 1 : position]]
            )
    return result


class _Templates:
    def __init__(self, index, discovery, paths, exclude):
        self.index = index
        self.discovery = discovery
        self.constants = _Constants(index)
        self.templates = {}
        self.aliases = {}
        self.libraries = {}
        self.builtins = []
        self.untyped_files = set()
        self.typed_files = set()
        self.remaining = 20000
        self._files(paths, exclude)
        self._settings()
        self._registrations()

    def _files(self, paths, exclude):
        seen = set()
        total = 0
        for path in paths:
            path = Path(path).resolve()
            if any(fnmatch.fnmatch(str(path), pattern) for pattern in exclude):
                continue
            if not path.exists():
                self.index.note(
                    f"Django template path does not exist: {path}."
                )
                continue
            if path.is_dir():
                try:
                    files = sorted(path.rglob("*.html"))
                except OSError as error:
                    self.index.note(
                        f"Cannot read Django template path {path}: {error}."
                    )
                    continue
            else:
                files = [path]
            for filename in files:
                if filename in seen or any(
                    fnmatch.fnmatch(str(filename), pattern)
                    for pattern in exclude
                ):
                    continue
                seen.add(filename)
                try:
                    if filename.stat().st_size > 1024 * 1024:
                        self.index.note(
                            "Django template is too large to analyze: "
                            f"{filename}."
                        )
                        continue
                    text = filename.read_text(encoding="utf-8")
                except (OSError, UnicodeError) as error:
                    self.index.note(
                        f"Cannot read Django template {filename}: {error}."
                    )
                    continue
                total += len(text)
                if len(seen) > 10000 or total > 16 * 1024 * 1024:
                    self.index.note("Django template scan budget exceeded.")
                    return
                names = {
                    filename.relative_to(path).as_posix()
                    if path.is_dir()
                    else filename.name
                }
                if "templates" in filename.parts:
                    position = (
                        len(filename.parts)
                        - 1
                        - filename.parts[::-1].index("templates")
                    )
                    names.add(Path(*filename.parts[position + 1 :]).as_posix())
                tokens = list(_tokens(text))
                for name in names:
                    previous = self.templates.get(name)
                    if previous and previous[0] != filename:
                        self.index.note(
                            f"Ambiguous Django template name {name}; "
                            "no typed context is inferred."
                        )
                        self.templates[name] = (None, [])
                    elif previous is None:
                        self.templates[name] = (filename, tokens)

    def _settings(self):
        settings_module = getattr(self.discovery, "settings_module", "")
        if not settings_module:
            return
        settings = self.constants.module(settings_module)
        value = settings.get("TEMPLATES")
        if value is None:
            return
        if not value.known or not isinstance(value.value, (list, tuple)):
            self.index.note(
                "Cannot resolve Django template libraries from TEMPLATES."
            )
            return
        for engine in value.value:
            if (
                not isinstance(engine, dict)
                or engine.get("BACKEND")
                != "django.template.backends.django.DjangoTemplates"
            ):
                continue
            options = engine.get("OPTIONS", {})
            if not isinstance(options, dict):
                self.index.note("Cannot resolve Django template OPTIONS.")
                continue
            libraries = options.get("libraries", {})
            if isinstance(libraries, dict):
                for alias, module in libraries.items():
                    if isinstance(alias, str) and isinstance(module, str):
                        if (
                            alias in self.aliases
                            and self.aliases[alias] != module
                        ):
                            self.aliases[alias] = None
                            self.index.note(
                                "Ambiguous configured Django template "
                                f"library {alias}."
                            )
                        else:
                            self.aliases[alias] = module
            else:
                self.index.note(
                    "Cannot resolve configured Django template libraries."
                )
            builtins = options.get("builtins", [])
            if isinstance(builtins, (list, tuple)) and all(
                isinstance(module, str) for module in builtins
            ):
                self.builtins.extend(builtins)
            else:
                self.index.note(
                    "Cannot resolve configured Django template builtins."
                )

    def _instance(self, expression, bindings):
        instance = self.index.resolve_expression(expression, bindings)
        if not isinstance(instance, _Instance):
            return None
        constructor = self.index.resolve(instance.constructor)
        if (
            constructor in _LIBRARIES
            if isinstance(constructor, str)
            else False
        ):
            return instance
        if isinstance(constructor, _Class) and any(
            base in _LIBRARIES
            for base in self.index.mro(constructor)
            if isinstance(base, str)
        ):
            return instance
        return None

    def _registrations(self):
        registries = {}
        events = []
        for function in self.index.functions:
            for decorator in function.node.decorator_list:
                call = decorator if isinstance(decorator, ast.Call) else None
                target = call.func if call else decorator
                events.append(
                    (
                        function.module,
                        decorator.lineno,
                        target,
                        call,
                        function.bindings,
                        function,
                    )
                )
        for module in self.index.modules.values():
            for call, bindings in module.calls:
                events.append(
                    (module.name, call.lineno, call.func, call, bindings, None)
                )
        for module, _, target, call, bindings, function in sorted(
            events, key=lambda event: (event[0], event[1])
        ):
            if (
                not isinstance(target, ast.Attribute)
                or target.attr not in _REGISTRATIONS
            ):
                continue
            instance = self._instance(target.value, bindings)
            if instance is None:
                continue
            registry = registries.setdefault(instance, _Library(module))
            kind = "filters" if target.attr == "filter" else "tags"
            explicit = None
            if call:
                keywords = {kw.arg: kw.value for kw in call.keywords}
                if "name" in keywords:
                    explicit = _literal(keywords["name"])
                elif (
                    target.attr in {"filter", "tag"}
                    and call.args
                    and isinstance(call.args[0], ast.Constant)
                    and isinstance(call.args[0].value, str)
                ):
                    explicit = call.args[0].value
                elif (
                    target.attr in {"simple_tag", "simple_block_tag"}
                    and len(call.args) > 2
                ):
                    explicit = _literal(call.args[2])
                elif target.attr == "inclusion_tag" and len(call.args) > 3:
                    explicit = _literal(call.args[3])
                if function is None:
                    callback = keywords.get(
                        "filter_func"
                        if kind == "filters"
                        else "compile_function"
                    )
                    if target.attr in {
                        "simple_tag",
                        "simple_block_tag",
                        "inclusion_tag",
                    }:
                        callback = keywords.get("func", callback)
                    if callback is None:
                        offset = (
                            1
                            if target.attr == "inclusion_tag"
                            or (
                                explicit is not None
                                and target.attr in {"filter", "tag"}
                            )
                            else 0
                        )
                        if len(call.args) > offset:
                            callback = call.args[offset]
                    function = self.index.resolve_expression(
                        callback, bindings
                    )
            if not isinstance(function, _Function) or (
                explicit is not None and not isinstance(explicit, str)
            ):
                self.index.note(
                    f"Cannot resolve Django template {target.attr} "
                    f"registration in {module}:{target.lineno}."
                )
                continue
            name = function.node.name if explicit is None else explicit
            getattr(registry, kind)[name] = (
                function if kind == "filters" else _Tag(function, target.attr)
            )
        for module in self.index.modules.values():
            instance = self._instance(
                ast.Name(id="register", ctx=ast.Load()), module.bindings
            )
            if instance in registries:
                self.libraries[module.name] = registries[instance]
            elif instance is not None:
                self.libraries[module.name] = _Library(module.name)

    def _library(self, name):
        if name in self.aliases:
            module = self.aliases[name]
        else:
            candidates = {
                f"{app}.templatetags.{name}"
                for app in self.discovery.apps
                if f"{app}.templatetags.{name}" in self.index.modules
            }
            if name in self.index.modules:
                candidates.add(name)
            if len(candidates) > 1:
                self.index.note(f"Ambiguous Django template library {name}.")
                return None
            module = next(iter(candidates), None)
        library = self.libraries.get(module)
        if library is None:
            self.index.note(
                f"Django template library {name} is outside the scan "
                "or has no resolvable register Library."
            )
        else:
            self.index.retain_module_member(
                module,
                "register",
                "django.template-library",
                f"Loaded Django template Library {name}.",
            )
        return library

    def _callback(self, callback, kind, filename):
        if callback:
            registration = callback if isinstance(callback, _Tag) else None
            if registration:
                callback = registration.function
            self.index.retain_symbol(
                callback,
                f"django.template-{kind}",
                f"Django template {filename} uses registered {kind} callback.",
            )
            if registration and registration.kind == "tag":
                body = [
                    node
                    for node in callback.node.body
                    if not isinstance(node, ast.Pass)
                    and not (
                        isinstance(node, ast.Expr)
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)
                    )
                ]
                parameters = {
                    arg.arg
                    for arg in [
                        *callback.node.args.posonlyargs,
                        *callback.node.args.args,
                        *callback.node.args.kwonlyargs,
                    ]
                }
                for node in body if len(body) == 1 else []:
                    if isinstance(node, (ast.Return, ast.Raise)):
                        if not isinstance(node, ast.Return) or not isinstance(
                            node.value, ast.Call
                        ):
                            break
                        cls = self.index.resolve_expression(
                            node.value.func,
                            self.index.modules[callback.module].bindings,
                        )
                        root = node.value.func
                        while isinstance(root, ast.Attribute):
                            root = root.value
                        if (
                            isinstance(root, ast.Name)
                            and root.id in parameters
                        ):
                            break
                        if isinstance(cls, _Class) and any(
                            base
                            in {
                                "django.template.Node",
                                "django.template.base.Node",
                            }
                            for base in self.index.mro(cls)
                            if isinstance(base, str)
                        ):
                            member = self.index.effective_member(cls, "render")
                            if member:
                                self.index.retain(
                                    *member,
                                    "django.template-node",
                                    "Node returned by used template tag "
                                    f"{callback.qualified_name}.",
                                )
                        break

    def _member(self, expression, context, filename):
        if not _PATH.fullmatch(expression):
            return None
        parts = expression.split(".")
        value = context.get(parts[0])
        if value is None:
            if len(parts) > 1:
                self.untyped_files.add(filename)
                if context:
                    self.index.note(
                        f"Some Django template context in {filename} "
                        "has no known model type; those dotted "
                        "references remain reportable."
                    )
            return None
        if len(parts) == 1:
            return value
        if value.many:
            # Indexing a literal position has the queryset element's type.
            if parts[1].isdigit():
                value = _Type(value.cls)
                parts.pop(1)
            else:
                return None
        for name in parts[1:]:
            member = self.index.effective_member(value.cls, name)
            if member is None:
                return None
            self.index.retain(
                *member,
                "django.template-context",
                f"Django template {filename} looks up "
                f"{value.cls.qualified_name}.{name} "
                "on a statically typed model context.",
            )
            # Return annotations and model relation traversal are deliberately
            # not treated as proof of the value returned by a runtime lookup.
            return None
        return value

    def _expression(self, expression, context, filters, filename):
        parts = _split(expression, "|")
        value = self._member(parts[0], context, filename)
        for part in parts[1:]:
            bits = _split(part, ":")
            self._callback(filters.get(bits[0]), "filter", filename)
            if len(bits) > 1:
                self._member(bits[1], context, filename)
        return value if len(parts) == 1 else None

    def _render(
        self, name, context, visiting=(), overrides=None, selected=None
    ):
        if self.remaining <= 0:
            self.index.note("Django template analysis budget exceeded.")
            return
        if name in visiting and selected is None:
            self.index.note(
                f"Cyclic Django template include/extends at {name}."
            )
            return
        template = self.templates.get(name)
        if template is None:
            self.index.note(
                f"Referenced Django template {name} "
                "is outside the template scan."
            )
            return
        filename, tokens = template
        if filename is None:
            return
        overrides = {} if overrides is None else overrides
        if selected is not None:
            tokens = selected
        if context:
            self.typed_files.add(filename)
        extends = next(
            (
                bits
                for block, _, bits in tokens
                if block and bits and bits[0] == "extends"
            ),
            None,
        )
        if extends:
            target = _quoted(extends[1]) if len(extends) > 1 else None
            if target is None:
                self.index.note(
                    f"Dynamic Django template extends in {filename} "
                    "cannot be resolved."
                )
            else:
                self._render(
                    target,
                    context,
                    (*visiting, name),
                    {**_blocks(name, tokens), **overrides},
                )
            return
        tags, filters = {}, {}
        for module in self.builtins:
            library = self._library(module)
            if library:
                tags.update(library.tags)
                filters.update(library.filters)
        context = context.copy()
        stack = []
        skipped = 0
        for block, content, bits in tokens:
            self.remaining -= 1
            if self.remaining <= 0:
                self.index.note("Django template analysis budget exceeded.")
                return
            if (
                block
                and bits
                and bits[0] == "block"
                and (skipped or (len(bits) > 1 and bits[1] in overrides))
            ):
                if not skipped:
                    replacement = overrides[bits[1]]
                    self._render(
                        replacement.source,
                        context,
                        (*visiting, name),
                        overrides,
                        replacement.tokens,
                    )
                skipped += 1
                continue
            if skipped:
                if block and bits and bits[0] == "endblock":
                    skipped -= 1
                continue
            if not block:
                if content == "block.super" and context:
                    self.index.note(
                        f"Django template block.super in {filename} "
                        "is not used to infer model member references."
                    )
                self._expression(content, context, filters, filename)
                continue
            if not bits:
                continue
            command, arguments = bits[0], bits[1:]
            if command in {"block", "endblock"}:
                continue
            if command == "load":
                if "from" in arguments:
                    position = arguments.index("from")
                    names, modules = (
                        arguments[:position],
                        arguments[position + 1 :],
                    )
                    if len(modules) != 1:
                        self.index.note(
                            "Cannot resolve Django template load "
                            f"statement in {filename}."
                        )
                        continue
                else:
                    names, modules = None, arguments
                for module in modules:
                    library = self._library(module)
                    if library:
                        tags.update(
                            (key, value)
                            for key, value in library.tags.items()
                            if names is None or key in names
                        )
                        filters.update(
                            (key, value)
                            for key, value in library.filters.items()
                            if names is None or key in names
                        )
                continue
            self._callback(tags.get(command), "tag", filename)
            if command in {"endfor", "endwith"}:
                if stack and stack[-1][0] == command:
                    _, context = stack.pop()
                continue
            if command == "empty":
                if stack and stack[-1][0] == "endfor":
                    context = stack[-1][1].copy()
                continue
            if command == "for" and "in" in arguments:
                position = arguments.index("in")
                if len(arguments) <= position + 1:
                    continue
                value = self._expression(
                    arguments[position + 1], context, filters, filename
                )
                stack.append(("endfor", context.copy()))
                for variable in (
                    " ".join(arguments[:position]).replace(",", " ").split()
                ):
                    context.pop(variable, None)
                if position == 1 and value and value.many:
                    context[arguments[0]] = _Type(value.cls)
                continue
            if command == "with":
                previous = context.copy()
                updates = self._bindings(
                    arguments, previous, filters, filename
                )
                stack.append(("endwith", previous))
                for key, value in updates.items():
                    context.pop(key, None)
                    if value:
                        context[key] = value
                continue
            if command in {"include", "extends"}:
                if not arguments:
                    continue
                target = _quoted(arguments[0])
                if target is None:
                    self.index.note(
                        f"Dynamic Django template {command} in "
                        f"{filename} cannot be resolved."
                    )
                    continue
                child = context.copy()
                if command == "include":
                    updates = self._bindings(
                        arguments[arguments.index("with") + 1 :]
                        if "with" in arguments
                        else [],
                        context,
                        filters,
                        filename,
                    )
                    if "only" in arguments:
                        child = {}
                    for key, value in updates.items():
                        child.pop(key, None)
                        if value:
                            child[key] = value
                self._render(target, child, (*visiting, name))
                continue
            tag = tags.get(command)
            expression_arguments = command in {
                "if",
                "elif",
                "url",
                "cycle",
                "firstof",
                "widthratio",
                "ifchanged",
            } or (tag and tag.kind != "tag")
            for expression in arguments if expression_arguments else []:
                # Literal text is ignored by _member; filter arguments still
                # resolve only actual filter syntax outside quotes.
                self._expression(
                    expression.split("=", 1)[-1], context, filters, filename
                )
            if "as" in arguments and arguments.index("as") + 1 < len(
                arguments
            ):
                context.pop(arguments[arguments.index("as") + 1], None)

    def _bindings(self, arguments, context, filters, filename):
        if len(arguments) == 3 and arguments[1] == "as":
            return {
                arguments[2]: self._expression(
                    arguments[0], context, filters, filename
                )
            }
        return {
            name: self._expression(expression, context, filters, filename)
            for argument in arguments
            if "=" in argument
            for name, expression in [argument.split("=", 1)]
        }

    def _attribute(self, cls, name, default=_UNKNOWN):
        member = self.index.effective_member(cls, name)
        if not member:
            return default
        if member[1].uncertain:
            return _UNKNOWN
        _, value = member
        if value.value is None:
            return _UNKNOWN
        # A class assignment captures its value immediately. Final module
        # constants can have changed afterwards, so aliases aren't proof.
        return _literal(value.value)

    def _view_contexts(self):
        for module in self.index.modules.values():
            if (
                module.name not in self.discovery.url_modules
                and module.name not in self.discovery.loaded_modules
            ):
                continue
            for call, bindings in module.calls:
                if (
                    not isinstance(call.func, ast.Attribute)
                    or call.func.attr != "as_view"
                ):
                    continue
                cls = self.index.resolve_expression(call.func.value, bindings)
                if not isinstance(
                    cls, _Class
                ) or "view" not in self.index.kind(cls):
                    continue
                options = {
                    keyword.arg: keyword.value for keyword in call.keywords
                }
                template = (
                    _literal(options["template_name"])
                    if "template_name" in options
                    else self._attribute(cls, "template_name")
                )
                if not isinstance(template, str):
                    continue
                if None in options:
                    self.index.note(
                        "Unknown as_view keyword overrides on "
                        f"{cls.qualified_name}; "
                        "template context cannot be inferred."
                    )
                    continue
                kinds = self.index.kind(cls)
                context = {}
                if kinds & {"single-object", "multiple-object"}:
                    if any(
                        isinstance(base, str)
                        and base not in framework.EXTERNAL_MEMBERS
                        and base not in contracts.EXTERNAL_MEMBERS
                        and base not in {"builtins.object", "builtins.type"}
                        for base in self.index.mro(cls)
                    ):
                        self.index.note(
                            f"Unresolved view base on {cls.qualified_name}; "
                            "template model context cannot be inferred."
                        )
                        continue
                    queryset = (
                        _literal(options["queryset"])
                        if "queryset" in options
                        else self._attribute(cls, "queryset", None)
                    )
                    if queryset is not None:
                        self.index.note(
                            f"Custom queryset on {cls.qualified_name}; "
                            "template model context cannot be inferred."
                        )
                        continue
                    overridable = {
                        "__init__",
                        "setup",
                        "dispatch",
                        "get",
                        "render_to_response",
                        "get_context_data",
                        "get_queryset",
                        "get_context_object_name",
                        "get_template_names",
                    }
                    if "single-object" in kinds:
                        overridable.add("get_object")
                    if set(options) & overridable or any(
                        self.index.effective_member(cls, method)
                        for method in overridable
                    ):
                        self.index.note(
                            "Custom context/queryset/template methods on "
                            f"{cls.qualified_name}; "
                            "template model context cannot be inferred."
                        )
                        yield template, context
                        continue
                    model = (
                        self.index.resolve_expression(
                            options["model"], bindings
                        )
                        if "model" in options
                        else None
                    )
                    if "model" not in options:
                        member = self.index.effective_member(cls, "model")
                        model = (
                            self.index.resolve(member[1].reference)
                            if member
                            else None
                        )
                    if isinstance(
                        model, _Class
                    ) and "model" in self.index.kind(model):
                        many = "multiple-object" in kinds
                        value = _Type(model, many)
                        context["object_list" if many else "object"] = value
                        context_name = (
                            _literal(options["context_object_name"])
                            if "context_object_name" in options
                            else self._attribute(
                                cls, "context_object_name", None
                            )
                        )
                        if context_name is not _UNKNOWN and not context_name:
                            context_name = model.node.name.lower() + (
                                "_list" if many else ""
                            )
                        if isinstance(context_name, str):
                            context[context_name] = value
                        extra = (
                            _literal(options["extra_context"])
                            if "extra_context" in options
                            else self._attribute(cls, "extra_context", None)
                        )
                        if extra is not None:
                            if isinstance(extra, dict):
                                for key in extra:
                                    context.pop(key, None)
                            else:
                                context = {}
                yield template, context

    def _instance_type(self, expression, bindings, typed):
        if isinstance(expression, ast.Name):
            return typed.get(expression.id)
        if isinstance(expression, ast.Call):
            cls = self.index.resolve_expression(expression.func, bindings)
            if isinstance(cls, _Class) and "model" in self.index.kind(cls):
                return _Type(cls)
        return None

    def _function_contexts(self):
        for function in self.index.functions:
            module = self.index.modules[function.module]
            bindings = module.bindings.copy()
            typed = {}
            local_nodes = list(_scope_nodes(function.node.body))
            globals_ = {
                name
                for node in local_nodes
                if isinstance(node, (ast.Global, ast.Nonlocal))
                for name in node.names
            }
            for node in local_nodes:
                names = []
                if isinstance(node, ast.Name) and isinstance(
                    node.ctx, (ast.Store, ast.Del)
                ):
                    names = [node.id]
                elif isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    names = [node.name]
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [
                        alias.asname or alias.name.split(".")[0]
                        for alias in node.names
                    ]
                elif (
                    isinstance(
                        node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)
                    )
                    and node.name
                ):
                    names = [node.name]
                elif isinstance(node, ast.MatchMapping) and node.rest:
                    names = [node.rest]
                for name in names:
                    if name not in globals_:
                        bindings[name] = None
            parameters = [
                *function.node.args.posonlyargs,
                *function.node.args.args,
                *function.node.args.kwonlyargs,
            ]
            parameters.extend(
                argument
                for argument in (
                    function.node.args.vararg,
                    function.node.args.kwarg,
                )
                if argument
            )
            for parameter in parameters:
                bindings[parameter.arg] = None
            yield from self._function_body(
                function.node.body, bindings, typed, module
            )

    @staticmethod
    def _invalidate(node, bindings, typed):
        for child in _scope_nodes([node]):
            target = child
            if isinstance(
                child, (ast.Attribute, ast.Subscript)
            ) and isinstance(child.ctx, (ast.Store, ast.Del)):
                while isinstance(target, (ast.Attribute, ast.Subscript)):
                    target = target.value
                if isinstance(target, ast.Name):
                    typed.pop(target.id, None)
            if isinstance(child, ast.Name) and isinstance(
                child.ctx, (ast.Store, ast.Del)
            ):
                typed.pop(child.id, None)
                bindings[child.id] = None

    def _function_body(self, nodes, bindings, typed, module):
        for node in nodes:
            if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                bindings[node.name] = None
                typed.pop(node.name, None)
                continue
            if isinstance(node, ast.If):
                if isinstance(node.test, ast.Constant):
                    chosen = node.body if node.test.value else node.orelse
                    yield from self._function_body(
                        chosen,
                        bindings,
                        typed,
                        module,
                    )
                    if _terminates(chosen):
                        return
                else:
                    self._invalidate(node, bindings, typed)
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                source = (
                    self.index._import_module(node, module)
                    if isinstance(node, ast.ImportFrom)
                    else None
                )
                for alias in node.names:
                    name = alias.asname or alias.name.split(".")[0]
                    if alias.name == "*":
                        bindings = dict.fromkeys(bindings)
                        typed.clear()
                    else:
                        bindings[name] = (
                            f"{source}.{alias.name}"
                            if source
                            else alias.name
                            if alias.asname
                            else name
                        )
                        typed.pop(name, None)
                continue
            if (
                isinstance(
                    node, (ast.Return, ast.Expr, ast.Assign, ast.AnnAssign)
                )
                and node.value is not None
            ):
                for call in self.index._calls(node.value):
                    target = self.index.resolve_expression(call.func, bindings)
                    if target != "django.shortcuts.render":
                        continue
                    keywords = {
                        keyword.arg: keyword.value for keyword in call.keywords
                    }
                    if None in keywords:
                        continue
                    template = (
                        call.args[1]
                        if len(call.args) > 1
                        else keywords.get("template_name")
                    )
                    name = _literal(template)
                    values = (
                        call.args[2]
                        if len(call.args) > 2
                        else keywords.get("context")
                    )
                    context = {}
                    if isinstance(values, ast.Dict) and all(
                        key is not None for key in values.keys
                    ):
                        for key, value in zip(
                            values.keys, values.values, strict=True
                        ):
                            key = _literal(key)
                            value = self._instance_type(value, bindings, typed)
                            if isinstance(key, str):
                                context.pop(key, None)
                                if value:
                                    context[key] = value
                    if isinstance(name, str):
                        yield name, context
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    value = self._instance_type(node.value, bindings, typed)
                    reference = self.index.resolve_expression(
                        node.value, bindings
                    )
                    targets = (
                        node.targets
                        if isinstance(node, ast.Assign)
                        else [node.target]
                    )
                    for target in targets:
                        if isinstance(target, ast.Name):
                            bindings[target.id] = reference
                            typed.pop(target.id, None)
                            if value:
                                typed[target.id] = value
                        else:
                            self._invalidate(target, bindings, typed)
                if isinstance(node, ast.Return):
                    return
            elif isinstance(node, (ast.Raise, ast.Break, ast.Continue)):
                return
            elif not isinstance(node, ast.Pass):
                self._invalidate(node, bindings, typed)

    def run(self):
        # Each template has its own parser library namespace. Untyped passes
        # still detect explicit tag/filter uses without guessing model owners.
        for name in self.templates:
            self._render(name, {})
        for name, context in (
            *self._view_contexts(),
            *self._function_contexts(),
        ):
            if context:
                self._render(name, context)
        for filename in sorted(self.untyped_files - self.typed_files):
            self.index.note(
                f"Untyped Django template context in {filename}; "
                "dotted references without a known model type "
                "remain reportable."
            )


def run(index, discovery, template_paths, exclude=()):
    if template_paths:
        _Templates(index, discovery, template_paths, exclude).run()
