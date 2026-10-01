"""Explain Django's implicit uses without importing application code.

The index intentionally resolves only ordinary imports, class declarations,
and simple aliases. Unknown expressions produce no exemption. Evidence is
attached to the same definition key used by Vulture, rather than to a name.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from vulture.lines import get_first_line_number

from . import contracts


@dataclass(frozen=True)
class Evidence:
    filename: Path
    lineno: int
    name: str
    qualified_name: str
    rule: str
    reason: str
    item_types: frozenset[str] = frozenset()
    node_id: int | None = None


@dataclass
class Analysis:
    evidence: dict[tuple[Path, int, str], Evidence] = field(
        default_factory=dict
    )
    notes: list[str] = field(default_factory=list)


@dataclass(eq=False)
class _Function:
    module: str
    filename: Path
    node: ast.FunctionDef | ast.AsyncFunctionDef
    qualified_name: str
    bindings: dict = field(default_factory=dict)


@dataclass(eq=False)
class _Instance:
    module: str
    filename: Path
    node: ast.Call
    constructor: str | _Class | _Function | None


@dataclass(eq=False)
class _Member:
    name: str
    node: ast.AST
    value: ast.AST | None = None
    reference: str | _Class | _Function | _Instance | None = None
    constructor: str | _Class | None = None
    uncertain: bool = False
    bindings: dict = field(default_factory=dict)


@dataclass(eq=False)
class _Class:
    module: str
    filename: Path
    node: ast.ClassDef
    qualified_name: str
    bases: list[str | _Class | None]
    members: dict[str, _Member] = field(default_factory=dict)
    bindings: dict = field(default_factory=dict)


@dataclass
class _Module:
    name: str
    filename: Path
    tree: ast.Module
    package: str
    bindings: dict = field(default_factory=dict)
    imports: set[tuple[str, str | None]] = field(default_factory=set)
    members: dict[str, _Member] = field(default_factory=dict)
    calls: list[tuple[ast.Call, dict]] = field(default_factory=list)

    @property
    def qualified_name(self):
        return self.name


class _Index:
    def __init__(self, modules, project_root):
        self.project_root = project_root
        self.modules: dict[str, _Module] = {}
        self.classes: list[_Class] = []
        self.functions: list[_Function] = []
        self._activated_functions: set[_Function] = set()
        self.result = Analysis()
        self._kinds: dict[_Class, set[str]] = {}
        self._mros: dict[_Class, list[str | _Class]] = {}
        self._noted: set[str] = set()
        for filename, tree in modules.items():
            filename = Path(filename)
            try:
                relative = filename.resolve().relative_to(
                    project_root.resolve()
                )
            except ValueError:
                self.note(
                    f"Cannot resolve module outside project root: {filename}"
                )
                continue
            parts = list(relative.with_suffix("").parts)
            is_package = parts[-1:] == ["__init__"]
            if is_package:
                parts.pop()
            name = ".".join(parts)
            package = name if is_package else name.rpartition(".")[0]
            self.modules[name] = _Module(name, filename, tree, package)
        self._binding_modules = {
            id(module.bindings): module for module in self.modules.values()
        }
        # Parsing never resolves another module's bindings until all modules
        # have been indexed. This makes scan order immaterial.
        for module in self.modules.values():
            self._body(module.tree.body, module.bindings, module)

    def note(self, text):
        if text not in self._noted:
            self.result.notes.append(text)
            self._noted.add(text)

    def _import_module(self, node, module):
        if not node.level:
            return node.module or ""
        parts = module.package.split(".") if module.package else []
        if node.level > len(parts):
            return None
        prefix = parts[: len(parts) - node.level + 1]
        if node.module:
            prefix.extend(node.module.split("."))
        return ".".join(prefix)

    def _reference(self, expression, bindings):
        if isinstance(expression, ast.Name):
            if expression.id in bindings:
                return bindings[expression.id]
            if expression.id in {"object", "type"}:
                return f"builtins.{expression.id}"
        elif isinstance(expression, ast.Attribute):
            base = self._reference(expression.value, bindings)
            if isinstance(base, str):
                return f"{base}.{expression.attr}"
            if isinstance(base, _Class):
                member = base.members.get(expression.attr)
                return member.reference if member else None
        return None

    def resolve_expression(self, expression, bindings):
        return self.resolve(self._reference(expression, bindings))

    @staticmethod
    def _calls(expression):
        if isinstance(
            expression,
            (
                ast.Lambda,
                ast.GeneratorExp,
                ast.ListComp,
                ast.SetComp,
                ast.DictComp,
                ast.BoolOp,
                ast.IfExp,
            ),
        ):
            return
        if isinstance(expression, ast.Call):
            yield expression
        for child in ast.iter_child_nodes(expression):
            yield from _Index._calls(child)

    def _body(self, nodes, bindings, module, owner=None):
        for node in nodes:
            if isinstance(
                node, (ast.Expr, ast.Assign, ast.AnnAssign, ast.AugAssign)
            ):
                value = node.value
                if value is not None:
                    module.calls.extend(
                        (call, bindings.copy()) for call in self._calls(value)
                    )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name.split(".")[0]
                    bindings[name] = alias.name if alias.asname else name
                    if owner is None:
                        module.members[name] = _Member(
                            name, node, reference=bindings[name]
                        )
                    module.imports.add((alias.name, None))
            elif isinstance(node, ast.ImportFrom):
                source = self._import_module(node, module)
                for alias in node.names:
                    if source:
                        module.imports.add((source, alias.name))
                    if alias.name == "*":
                        # Star imports can override any preceding binding.
                        # Explicit definitions after this remain resolvable.
                        for name in bindings:
                            bindings[name] = None
                        if owner is None:
                            for name in module.members:
                                module.members[name].uncertain = True
                        if owner:
                            for name in owner.members:
                                owner.members[name] = _Member(
                                    name, node, uncertain=True
                                )
                        self.note(
                            f"Star import in {module.filename}:{node.lineno} "
                            "cannot be resolved statically."
                        )
                        continue
                    bindings[alias.asname or alias.name] = (
                        f"{source}.{alias.name}" if source else None
                    )
                    if owner is None:
                        name = alias.asname or alias.name
                        module.members[name] = _Member(
                            name, node, reference=bindings[name]
                        )
            elif isinstance(node, ast.ClassDef):
                qualified_name = (
                    f"{owner.qualified_name}.{node.name}"
                    if owner
                    else f"{module.name}.{node.name}".lstrip(".")
                )
                cls = _Class(
                    module.name,
                    module.filename,
                    node,
                    qualified_name,
                    [self._reference(base, bindings) for base in node.bases],
                    bindings=bindings.copy(),
                )
                self.classes.append(cls)
                class_bindings = bindings.copy()
                self._body(node.body, class_bindings, module, owner=cls)
                bindings[node.name] = cls
                if owner:
                    owner.members[node.name] = _Member(
                        node.name, node, reference=cls
                    )
                else:
                    module.members[node.name] = _Member(
                        node.name, node, reference=cls
                    )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified_name = (
                    f"{owner.qualified_name}.{node.name}"
                    if owner
                    else f"{module.name}.{node.name}".lstrip(".")
                )
                function = _Function(
                    module.name,
                    module.filename,
                    node,
                    qualified_name,
                    bindings.copy(),
                )
                self.functions.append(function)
                bindings[node.name] = function
                members = owner.members if owner else module.members
                members[node.name] = _Member(
                    node.name, node, reference=function
                )
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = (
                    node.targets
                    if isinstance(node, ast.Assign)
                    else [node.target]
                )
                value = node.value
                reference = self._reference(value, bindings)
                constructor = (
                    self._reference(value.func, bindings)
                    if isinstance(value, ast.Call)
                    else None
                )
                if isinstance(value, ast.Call):
                    reference = _Instance(
                        module.name, module.filename, value, constructor
                    )
                for target in targets:
                    if isinstance(target, ast.Name):
                        snapshot = bindings.copy()
                        bindings[target.id] = reference
                        members = owner.members if owner else module.members
                        members[target.id] = _Member(
                            target.id,
                            target,
                            value,
                            reference,
                            constructor,
                            bindings=snapshot,
                        )
                    else:
                        self._invalidate(target, bindings, owner)
            elif isinstance(node, ast.If) and isinstance(
                node.test, ast.Constant
            ):
                self._body(
                    node.body if node.test.value else node.orelse,
                    bindings,
                    module,
                    owner,
                )
            else:
                # Conditional imports/assignments and loops may shadow names.
                # Do not treat either branch as an unconditional definition.
                self._invalidate(node, bindings, owner)

    def _invalidate_name(self, name, node, bindings, owner):
        if name:
            bindings[name] = None
            if owner:
                owner.members[name] = _Member(name, node, uncertain=True)
            else:
                module = self._binding_modules.get(id(bindings))
                if module:
                    module.members[name] = _Member(name, node, uncertain=True)

    def _invalidate(self, node, bindings, owner=None):
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            self._invalidate_name(node.name, node, bindings, owner)
            return
        if isinstance(node, ast.Name) and isinstance(
            node.ctx, (ast.Store, ast.Del)
        ):
            self._invalidate_name(node.id, node, bindings, owner)
        elif isinstance(node, ast.Attribute) and isinstance(
            node.ctx, (ast.Store, ast.Del)
        ):
            root = node.value
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name):
                self._invalidate_name(root.id, node, bindings, owner)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    for name in bindings:
                        bindings[name] = None
                    if owner:
                        for name in owner.members:
                            owner.members[name] = _Member(
                                name, node, uncertain=True
                            )
                    continue
                self._invalidate_name(
                    alias.asname or alias.name.split(".")[0],
                    node,
                    bindings,
                    owner,
                )
        elif isinstance(node, (ast.MatchAs, ast.MatchStar, ast.ExceptHandler)):
            self._invalidate_name(node.name, node, bindings, owner)
        elif isinstance(node, ast.MatchMapping):
            self._invalidate_name(node.rest, node, bindings, owner)
        for child in ast.iter_child_nodes(node):
            self._invalidate(child, bindings, owner)

    def resolve(self, reference, seen=None):
        from . import framework

        if not isinstance(reference, str):
            return reference
        reference = framework.CANONICAL_BASES.get(
            reference, contracts.CANONICAL_BASES.get(reference, reference)
        )
        seen = set() if seen is None else seen
        if reference in seen:
            return None
        seen = seen | {reference}
        parts = reference.split(".")
        for length in range(len(parts), 0, -1):
            module_name = ".".join(parts[:length])
            module = self.modules.get(module_name)
            if module is None:
                continue
            remainder = parts[length:]
            if not remainder:
                return module_name
            if remainder[0] not in module.bindings:
                # ``from package import child_module`` can load a submodule
                # that isn't explicitly exported by package.__init__.
                child_name = f"{module_name}.{remainder[0]}"
                if child_name in self.modules:
                    return self.resolve(
                        ".".join([child_name, *remainder[1:]]), seen
                    )
                return None
            value = self.resolve(module.bindings[remainder[0]], seen)
            for name in remainder[1:]:
                if isinstance(value, _Class):
                    member = self.effective_member(value, name)
                    value = (
                        self.resolve(member[1].reference, seen)
                        if member
                        else None
                    )
                elif isinstance(value, str):
                    value = self.resolve(f"{value}.{name}", seen)
                else:
                    return None
            return value
        return reference

    def kind(self, value, seen=None):
        from . import framework

        value = self.resolve(value)
        if isinstance(value, str):
            kinds = set(framework.kinds(value))
            if value in contracts.MODEL_BASES:
                kinds.add("model")
            if value in contracts.FORM_BASES:
                kinds.add("form")
            if value in contracts.MODEL_FORM_BASES:
                kinds.update({"form", "model-form"})
            if value in contracts.MODEL_FIELDS:
                kinds.add("model-field")
            if value in contracts.FORM_FIELDS:
                kinds.add("form-field")
            if value in contracts.MODEL_MANAGERS:
                kinds.add("model-manager")
            return kinds
        if not isinstance(value, _Class):
            return set()
        if value in self._kinds:
            return self._kinds[value]
        seen = set() if seen is None else seen
        if value in seen:
            return set()
        kinds = set()
        for base in value.bases:
            kinds.update(self.kind(base, seen | {value}))
        self._kinds[value] = kinds
        return kinds

    def mro(self, cls, seen=None):
        from . import framework

        if cls in self._mros:
            return self._mros[cls]
        seen = set() if seen is None else seen
        if cls in seen:
            self.note(f"Cyclic inheritance in {cls.qualified_name}.")
            return [cls]
        bases = [self.resolve(base) for base in cls.bases] or [
            "builtins.object"
        ]
        sequences = []
        for index, base in enumerate(bases):
            if isinstance(base, _Class):
                sequences.append(self.mro(base, seen | {cls}).copy())
            elif isinstance(base, str):
                sequences.append(
                    [
                        base,
                        *framework.EXTERNAL_BASES.get(
                            base, contracts.EXTERNAL_BASES.get(base, ())
                        ),
                    ]
                )
            else:
                self.note(
                    f"Unresolved base in {cls.qualified_name}; inherited "
                    "Django members may be missed."
                )
                # Preserve an unresolved base's place in lookup order.
                bases[index] = f"<unresolved:{cls.qualified_name}:{index}>"
                sequences.append([bases[index]])
        sequences.append(bases.copy())
        result = [cls]
        while sequences:
            sequences = [sequence for sequence in sequences if sequence]
            if not sequences:
                break
            candidate = next(
                (
                    sequence[0]
                    for sequence in sequences
                    if not any(sequence[0] in other[1:] for other in sequences)
                ),
                None,
            )
            if candidate is None:
                self.note(
                    f"Cannot resolve method order in {cls.qualified_name}."
                )
                break
            result.append(candidate)
            for sequence in sequences:
                if sequence and sequence[0] == candidate:
                    sequence.pop(0)
        self._mros[cls] = result
        return result

    def effective_member(self, cls, name):
        from . import framework

        for base in self.mro(cls):
            if isinstance(base, _Class):
                if name in base.members:
                    return base, base.members[name]
            elif base in contracts.EXTERNAL_MEMBERS and (
                name in contracts.EXTERNAL_MEMBERS[base]
            ):
                return None
            elif (
                name in framework.known_members(base)
                if base in framework.EXTERNAL_MEMBERS
                else (
                    base not in contracts.EXTERNAL_MEMBERS
                    and name in self.hooks(self.kind(base))
                )
            ):
                # A framework implementation precedes a later ordinary mixin.
                return None
            elif (
                not self.kind(base)
                and base not in contracts.EXTERNAL_MEMBERS
                and base not in framework.EXTERNAL_MEMBERS
                and base not in {"builtins.object", "builtins.type"}
            ):
                # An external or computed base may override the member.
                self.note(
                    f"Unresolved inherited member lookup in "
                    f"{cls.qualified_name} through {base}."
                )
                return None
        return None

    @staticmethod
    def hooks(kinds):
        from . import framework

        names = set(framework.hooks(kinds))
        if "model" in kinds:
            names.update(contracts.MODEL_HOOKS)
        if "form" in kinds:
            names.update(contracts.FORM_HOOKS)
        if "model-form" in kinds:
            names.update(contracts.MODEL_FORM_HOOKS)
        return names

    def retain(self, owner, member, rule, reason):
        if member.uncertain:
            return
        lineno = get_first_line_number(member.node)
        evidence = Evidence(
            owner.filename,
            lineno,
            member.name,
            f"{owner.qualified_name}.{member.name}",
            rule,
            reason,
            self._item_types(member.node),
            id(member.node),
        )
        self.result.evidence.setdefault(
            (owner.filename, lineno, member.name), evidence
        )

    def retain_class(self, cls, rule, reason):
        lineno = get_first_line_number(cls.node)
        self.result.evidence.setdefault(
            (cls.filename, lineno, cls.node.name),
            Evidence(
                cls.filename,
                lineno,
                cls.node.name,
                cls.qualified_name,
                rule,
                reason,
                frozenset({"class"}),
                id(cls.node),
            ),
        )

    @staticmethod
    def _item_types(node):
        if isinstance(node, ast.ClassDef):
            return frozenset({"class"})
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return frozenset({"function", "method", "property"})
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return frozenset({"import"})
        return frozenset({"variable", "attribute"})

    def retain_module_member(self, module, name, rule, reason):
        module = (
            self.modules.get(module) if isinstance(module, str) else module
        )
        if module is not None and name in module.members:
            self.retain(module, module.members[name], rule, reason)

    def retain_symbol(self, reference, rule, reason):
        self._retain_reexports(reference, rule, reason)
        target = self.resolve(reference)
        if isinstance(target, _Class):
            self.retain_class(target, rule, reason)
            return target
        if isinstance(target, _Function):
            lineno = get_first_line_number(target.node)
            self.result.evidence.setdefault(
                (target.filename, lineno, target.node.name),
                Evidence(
                    target.filename,
                    lineno,
                    target.node.name,
                    target.qualified_name,
                    rule,
                    reason,
                    self._item_types(target.node),
                    id(target.node),
                ),
            )
            return target
        if isinstance(reference, str):
            module_name, _, name = reference.rpartition(".")
            module = self.modules.get(module_name)
            if module and name in module.members:
                member = module.members[name]
                if not member.uncertain:
                    self.retain(module, member, rule, reason)
                    return member
        return None

    def _retain_reexports(self, reference, rule, reason, seen=None):
        if not isinstance(reference, str):
            return
        seen = set() if seen is None else seen
        if reference in seen:
            return
        seen = seen | {reference}
        parts = reference.split(".")
        for length in range(len(parts) - 1, 0, -1):
            module = self.modules.get(".".join(parts[:length]))
            if module is None:
                continue
            member = module.members.get(parts[length])
            if member is None or member.uncertain:
                return
            if isinstance(
                member.node,
                (ast.Name, ast.Attribute, ast.Import, ast.ImportFrom),
            ):
                self.retain(module, member, rule, reason)
            if isinstance(member.reference, str):
                remainder = parts[length + 1 :]
                target = ".".join([member.reference, *remainder])
                self._retain_reexports(target, rule, reason, seen)
            return

    def activate_function(self, function):
        """Collect calls in an explicitly selected framework entry hook.

        Local imports and assignments are resolved in execution order. Unknown
        branches and nested functions are never treated as unconditional calls.
        """
        if (
            not isinstance(function, _Function)
            or function in self._activated_functions
        ):
            return
        self._activated_functions.add(function)
        module = self.modules[function.module]
        bindings = module.bindings.copy()
        args = function.node.args
        parameters = [*args.posonlyargs, *args.args, *args.kwonlyargs]
        parameters.extend(arg for arg in (args.vararg, args.kwarg) if arg)
        for parameter in parameters:
            bindings[parameter.arg] = None

        def local_names(nodes):
            for node in nodes:
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    yield node.name
                    continue
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    for alias in node.names:
                        yield alias.asname or alias.name.split(".")[0]
                elif isinstance(node, ast.Name) and isinstance(
                    node.ctx, (ast.Store, ast.Del)
                ):
                    yield node.id
                elif isinstance(
                    node, (ast.MatchAs, ast.MatchStar, ast.ExceptHandler)
                ):
                    if node.name:
                        yield node.name
                elif isinstance(node, ast.MatchMapping) and node.rest:
                    yield node.rest
                yield from local_names(ast.iter_child_nodes(node))

        for name in local_names(function.node.body):
            bindings[name] = None
        global_names = {
            name
            for node in ast.walk(function.node)
            if isinstance(node, ast.Global)
            for name in node.names
        }
        for name in global_names:
            bindings[name] = module.bindings.get(name)

        def body(nodes):
            for node in nodes:
                if isinstance(node, (ast.Return, ast.Raise)):
                    return False
                if isinstance(node, ast.If) and isinstance(
                    node.test, ast.Constant
                ):
                    if not body(node.body if node.test.value else node.orelse):
                        return False
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        name = alias.asname or alias.name.split(".")[0]
                        bindings[name] = alias.name if alias.asname else name
                elif isinstance(node, ast.ImportFrom):
                    source = self._import_module(node, module)
                    for alias in node.names:
                        if alias.name == "*":
                            for name in bindings:
                                bindings[name] = None
                        else:
                            bindings[alias.asname or alias.name] = (
                                f"{source}.{alias.name}" if source else None
                            )
                elif isinstance(
                    node, (ast.Expr, ast.Assign, ast.AnnAssign, ast.AugAssign)
                ):
                    if node.value is not None:
                        module.calls.extend(
                            (call, bindings.copy())
                            for call in self._calls(node.value)
                        )
                    if isinstance(node, (ast.Assign, ast.AnnAssign)):
                        reference = self._reference(node.value, bindings)
                        if isinstance(node.value, ast.Call):
                            reference = _Instance(
                                module.name,
                                module.filename,
                                node.value,
                                self._reference(node.value.func, bindings),
                            )
                        targets = (
                            node.targets
                            if isinstance(node, ast.Assign)
                            else [node.target]
                        )
                        for target in targets:
                            if isinstance(target, ast.Name):
                                bindings[target.id] = reference
                            else:
                                self._invalidate(target, bindings)
                    elif isinstance(node, ast.AugAssign):
                        self._invalidate(node.target, bindings)
                else:
                    self._invalidate(node, bindings)
            return True

        body(function.node.body)

    def declared_fields(self, cls, kind):
        fields = {}
        for base in reversed(self.mro(cls)):
            if not isinstance(base, _Class) or kind not in self.kind(base):
                continue
            for name, member in base.members.items():
                if member.uncertain:
                    fields.pop(name, None)
                elif f"{kind}-field" in self.kind(member.constructor):
                    fields[name] = (base, member)
                elif (
                    kind == "form"
                    and isinstance(member.value, ast.Constant)
                    and member.value.value is None
                ):
                    fields.pop(name, None)
        return fields

    def meta(self, cls):
        member = self.effective_member(cls, "Meta")
        if member:
            value = self.resolve(member[1].reference)
            if isinstance(value, _Class):
                return value
        return None

    def meta_value(self, cls, name):
        meta = self.meta(cls)
        if meta:
            return self.effective_member(meta, name)
        return None

    @staticmethod
    def literal(member):
        return _Index.static_literal(member)[1]

    @staticmethod
    def static_literal(member):
        if not member or member[1].value is None:
            return True, None
        try:
            return True, ast.literal_eval(member[1].value)
        except (ValueError, TypeError):
            return False, None

    def generated_form_field(self, member):
        reference = self.resolve(member.constructor)
        ancestry = (
            self.mro(reference)
            if isinstance(reference, _Class)
            else [reference]
        )
        if any(
            base in contracts.MODEL_FORM_EXCLUDED_FIELDS
            for base in ancestry
            if isinstance(base, str)
        ):
            return False
        keywords = (
            {keyword.arg: keyword.value for keyword in member.value.keywords}
            if isinstance(member.value, ast.Call)
            else {}
        )
        if "editable" in keywords:
            editable = keywords["editable"]
            if not isinstance(editable, ast.Constant) or not editable.value:
                return False
        elif any(
            base in contracts.MODEL_FIELDS_DEFAULT_NONEDITABLE
            for base in ancestry
            if isinstance(base, str)
        ):
            return False
        for flag in ("auto_now", "auto_now_add"):
            if flag in keywords and not (
                isinstance(keywords[flag], ast.Constant)
                and not keywords[flag].value
            ):
                return False
        return True

    def form_fields(self, cls):
        fields = set(self.declared_fields(cls, "form"))
        if "model-form" not in self.kind(cls):
            return fields
        model_member = self.meta_value(cls, "model")
        model = (
            self.resolve(model_member[1].reference) if model_member else None
        )
        if not isinstance(model, _Class) or "model" not in self.kind(model):
            self.note(
                f"Cannot resolve ModelForm model for {cls.qualified_name}; "
                "generated field cleaners may be missed."
            )
            return fields
        model_fields = self.declared_fields(model, "model")
        names = {
            name
            for name, (_, member) in model_fields.items()
            if self.generated_form_field(member)
        }
        selected_known, selected = self.static_literal(
            self.meta_value(cls, "fields")
        )
        excluded_member = self.meta_value(cls, "exclude")
        excluded_known, excluded = self.static_literal(excluded_member)
        if not selected_known or not excluded_known:
            self.note(
                f"Cannot resolve ModelForm field selection for "
                f"{cls.qualified_name}; generated field cleaners "
                "may be missed."
            )
            return fields
        if selected == "__all__" or (
            selected is None and isinstance(excluded, (tuple, list))
        ):
            generated = names
        elif isinstance(selected, (tuple, list)) and all(
            isinstance(name, str) for name in selected
        ):
            generated = names.intersection(selected)
        else:
            self.note(
                f"Cannot resolve ModelForm field selection for "
                f"{cls.qualified_name}; generated field cleaners "
                "may be missed."
            )
            generated = set()
        if isinstance(excluded, (tuple, list)) and all(
            isinstance(name, str) for name in excluded
        ):
            generated.difference_update(excluded)
        elif excluded_member and excluded is not None:
            generated = set()
        return fields | generated

    def model_modules(self, apps):
        roots = []
        if not apps:
            self.note(
                "No Django app packages configured; model registration "
                "cannot be established."
            )
        for app in apps:
            package = app
            configuration = self.resolve(app)
            if isinstance(configuration, _Class):
                name = self.effective_member(configuration, "name")
                package = self.literal(name)
                if not isinstance(package, str):
                    self.note(f"Cannot resolve Django app package for {app}.")
                    continue
            root = f"{package}.models"
            if root in self.modules:
                roots.append(root)
            elif not package.startswith("django."):
                self.note(
                    f"Django models module {root} is outside the scan or "
                    "does not exist; model registration may be missed."
                )
        return self.import_closure(roots)

    def import_closure(self, roots):
        reached = set()
        pending = []
        for root in sorted(set(roots)):
            parts = root.split(".")
            pending.extend(
                ".".join(parts[:length])
                for length in range(1, len(parts) + 1)
                if ".".join(parts[:length]) in self.modules
            )
        while pending:
            name = pending.pop()
            if name in reached or name not in self.modules:
                continue
            reached.add(name)
            module = self.modules[name]
            for source, alias in module.imports:
                imported_modules = [source]
                package = self.modules.get(source)
                if alias and alias != "*" and package:
                    if alias in package.bindings:
                        exported = self.resolve(package.bindings[alias])
                        if (
                            isinstance(exported, str)
                            and exported in self.modules
                        ):
                            imported_modules.append(exported)
                    elif "__getattr__" not in package.bindings:
                        candidate = f"{source}.{alias}"
                        if candidate in self.modules:
                            imported_modules.append(candidate)
                elif alias and alias != "*" and not package:
                    initializer = self.project_root.joinpath(
                        *source.split("."), "__init__.py"
                    )
                    candidate = f"{source}.{alias}"
                    # A namespace package has no initializer that could
                    # provide a scalar or dynamic export instead. An excluded
                    # initializer prevents that conclusion.
                    if not initializer.is_file() and candidate in self.modules:
                        imported_modules.append(candidate)
                # Importing a submodule executes its package initializers.
                for imported in imported_modules:
                    parts = imported.split(".")
                    pending.extend(
                        ".".join(parts[:length])
                        for length in range(1, len(parts) + 1)
                        if ".".join(parts[:length]) in self.modules
                    )
        return reached

    def abstract(self, cls, seen=None):
        own_meta = cls.members.get("Meta")
        meta = self.resolve(own_meta.reference) if own_meta else None
        if not isinstance(meta, _Class):
            return False if not own_meta else None
        seen = set() if seen is None else seen
        if cls in seen:
            return None
        # ModelBase reads abstract from the complete own Meta inheritance
        # chain, then writes abstract=False back to an abstract model's Meta.
        # Account for that reset when another class later extends Base.Meta.
        for base in self.mro(meta):
            if not isinstance(base, _Class):
                if base != "builtins.object":
                    return None
                continue
            for model in self.classes:
                if model is cls or model in seen:
                    continue
                if (
                    model.filename == cls.filename
                    and model.node.lineno >= cls.node.lineno
                ):
                    continue
                if "model" not in self.kind(model):
                    continue
                model_meta = model.members.get("Meta")
                if (
                    model_meta
                    and self.resolve(model_meta.reference) is base
                    and self.abstract(model, seen | {cls}) is True
                ):
                    return False
            if "abstract" in base.members:
                known, value = self.static_literal(
                    (base, base.members["abstract"])
                )
                return bool(value) if known else None
        return False

    def run(
        self, apps, settings_module="", template_paths=(), template_exclude=()
    ):
        from . import discovery, framework, registrations

        context = discovery.discover(self, apps, settings_module)
        loaded_models = (
            self.model_modules(context.apps) | context.loaded_modules
        )
        for cls in self.classes:
            kinds = self.kind(cls)
            if not kinds.intersection({"model", "form"}):
                continue
            if "model" in kinds and cls.module in loaded_models:
                # Abstract model classes aren't registered by ModelBase.
                # Django resets their Meta.abstract afterwards, so subclasses
                # don't inherit an abstract=True flag implicitly.
                abstract = self.abstract(cls)
                if abstract is False:
                    self.retain_class(
                        cls,
                        "django.model-registration",
                        f"Model imported by configured Django entry points "
                        f"in module {cls.module} "
                        f"(Django {contracts.DJANGO_VERSION}).",
                    )
                elif abstract is not True:
                    self.note(
                        f"Cannot resolve Meta.abstract for "
                        f"{cls.qualified_name}; model registration "
                        "is uncertain."
                    )
            for kind in ("model", "form"):
                if kind not in kinds:
                    continue
                for owner, member in self.declared_fields(cls, kind).values():
                    self.retain(
                        owner,
                        member,
                        f"django.{kind}-field",
                        f"Field declaration consumed by Django's {kind} "
                        f"metaclass for {cls.qualified_name}.",
                    )
            if "model" in kinds:
                for name in cls.members:
                    member = cls.members[name]
                    if "model-manager" in self.kind(member.constructor):
                        self.retain(
                            cls,
                            member,
                            "django.model-manager",
                            "Manager declaration consumed by ModelBase.",
                        )
            hooks = self.hooks(kinds)
            if "form" in kinds:
                hooks.update(f"clean_{name}" for name in self.form_fields(cls))
            for name in hooks:
                resolved = self.effective_member(cls, name)
                if resolved and isinstance(
                    resolved[1].node, (ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    owner, member = resolved
                    self.retain(
                        owner,
                        member,
                        "django.framework-hook",
                        f"Django {contracts.DJANGO_VERSION} hook on "
                        f"{cls.qualified_name}, which inherits a Django "
                        f"{'model' if 'model' in kinds else 'form'} base.",
                    )
            options = set()
            if "model" in kinds:
                options.update(contracts.MODEL_META_OPTIONS)
            if "model-form" in kinds:
                options.update(contracts.MODEL_FORM_META_OPTIONS)
            meta = self.meta(cls) if options else None
            if meta:
                self.retain_class(
                    meta,
                    "django.meta",
                    f"Meta configuration consumed by {cls.qualified_name}.",
                )
                for option in options:
                    resolved = self.effective_member(meta, option)
                    if resolved:
                        owner, member = resolved
                        self.retain(
                            owner,
                            member,
                            "django.meta-option",
                            f"Documented Django {contracts.DJANGO_VERSION} "
                            f"Meta option consumed by {cls.qualified_name}.",
                        )
        framework.run(self)
        registrations.run(self, context)
        if template_paths:
            from . import templates

            templates.run(
                self, context, template_paths, exclude=template_exclude
            )
        return self.result


def analyze(
    modules: dict[Path, ast.Module],
    apps: list[str],
    project_root: Path,
    settings_module: str = "",
    template_paths=(),
    template_exclude=(),
) -> Analysis:
    """Return scoped evidence and coverage notes for a parsed scan."""
    return _Index(modules, Path(project_root)).run(
        apps, settings_module, template_paths, template_exclude
    )
