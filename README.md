# Voldemort — find dead code in Django projects

Voldemort extends [Vulture](https://github.com/jendrikseipp/vulture) with static
knowledge of Django's conventions. It recognizes specific definitions consumed
by Django without marking every matching name in your project as used.

Supports **models, forms, views, admin, signals, and Django startup conventions**,
with opt-in template scanning and runtime call evidence. Static analysis never
imports your project, calls `django.setup()`, or requires Django to be installed.

## Installation and usage

Requires Python 3.10 or newer. Install this checkout:

```sh
python -m pip install -e .
voldemort . --django-app shop
# Discover installed apps and configured callbacks from a settings module:
voldemort . --django-settings project.settings --explain
# Equivalent without installing a console script:
python -m voldemort . --django-app shop
```

Voldemort provides both the `voldemort` and `vulture` commands and packages.
Use a separate virtual environment if you also use the upstream Vulture
distribution, since both distributions install the same `vulture` files.

Run from the directory containing your app packages. Select a settings module
with `--django-settings`, or repeat `--django-app` for each installed app, using
an app package (`shop`) or AppConfig path (`shop.apps.ShopConfig`). Explicit apps
override the settings module's `INSTALLED_APPS` for discovery. Use
`--project-root` if your packages live elsewhere, such as `src/`. Scan all relevant
source modules so aliases, inheritance, and imports can resolve.

```sh
voldemort src/ --project-root src --django-app project.shop --explain
```

`--explain` writes retained definitions, rules, and reasons to stderr. Dead-code
findings remain on stdout in Vulture's existing format. Coverage notes on stderr
identify cases the static Django rules cannot resolve.

Exit codes match Vulture: 0 for no findings, 3 for dead code, 1 for invalid input,
and 2 for invalid command-line arguments or configuration.

## Django support

```python
from django.db import models

class Book(models.Model):
    title = models.CharField(max_length=200)  # Django field declaration

    def clean(self):                         # Django validation hook
        spare = 42                          # Still reported as unused

class Ordinary:
    def clean(self):                        # Still reported as unused
        pass
```

Each exemption identifies a declaration by file, line, and name. Voldemort does
not add Django magic names to Vulture's global whitelist. Its rules cover:

- Known Django model/form fields and statically resolved local field subclasses.
- Model and form hooks, including hooks inherited from local mixins.
- Form `clean_<field>` methods when the field actually exists, including inherited
  fields and statically resolved ModelForm fields.
- Documented model and ModelForm `Meta` options; misspellings remain candidates.
- Model classes discovered from configured apps' `models.py` or
  `models/__init__.py`, following resolvable imports into scanned modules.
- Import aliases, relative imports, and inheritance across scanned files.
- Class-based view hooks and documented configuration appropriate to their
  resolved Django base, including local mixins and cooperative `super()` calls.
- Selected AppConfig classes, `ready()` imports, and their registration effects.
- Admin registrations, scoped display/action/readonly callbacks, inlines, custom
  admin sites, and admin `Media` configuration. `@admin.display` and `@admin.action`
  alone do not make a callable used.
- Signal receivers registered through resolved decorators or `connect()` calls.
- Management command and migration entry classes in installed apps, with
  documented hooks and attributes. Ordinary helpers remain reportable.
- Recognized settings strings for middleware, authentication backends, context
  processors, WSGI/ASGI callables, custom admin sites, and URL configurations.
- URL `include()` references, resolver metadata, and configured error handlers.
- Template references to registered custom tags and filters, and methods or
  properties reached through statically known view context types.

Settings discovery understands bounded literal expressions, simple aliases,
list/tuple concatenation, dictionary composition, and imports from scanned base
settings. Environment-dependent function calls are not evaluated. Mutated or
unresolved values produce coverage notes rather than broad exemptions.

A form class is not automatically used because it subclasses `Form`. Likewise,
an unimported `models_extra.py` is not automatically active because it belongs to
an installed app. Retained model fields describe schema; retaining a field does
not prove that application code queries it or that its database column is safe
to remove.

Framework exemptions leave unused locals, imports, parameters, arbitrary methods,
and unreachable code visible. Callback parameters are not automatically exempted.

## Templates

Select template directories or files explicitly. Directories are searched for
`.html` files; an explicitly selected file can have any extension. Repeat
`--django-template` to scan multiple locations:

```sh
voldemort . --django-settings project.settings \
  --django-template shop/templates --explain
```

`{% load %}` references resolve against installed apps' `templatetags` modules
and statically configured template libraries. Only tags and filters used in a
template retain their registered callables. Comment and verbatim blocks do not
create references.

Dotted lookups can retain methods and properties when the context object's
class is known from a class-based view or a supported literal `render()` call.
For example, `{{ object.display_title }}` can retain `Book.display_title` when
a `DetailView` declares `model = Book` and selects that template. An untyped
`{{ object.display_title }}` does not retain every method with that name.
Unresolved context and dynamic template selection produce coverage notes.
The Python scan's `--exclude` patterns also apply to selected templates.

Context type inference requires literal template/context attributes and default
Django view behavior. Overrides of request handling, queryset, context, or
rendering methods cancel inference. Values returned by model methods or tag
callbacks, model relation traversal, and `block.super` are outside the current
inference rules.

## Runtime evidence

The recorder explicitly executes a script or module under a Python call
profiler. Run it with the dependencies and environment needed by your project:

```sh
python -m voldemort.runtime --output .voldemort-calls.json \
  --project-root . --script manage.py -- test
# Or run a module, such as your existing test suite:
python -m voldemort.runtime --output .voldemort-calls.json \
  --project-root . --module pytest -- -q
```

Pass the resulting file to the normal analyzer; repeat `--runtime-evidence` to
combine recordings:

```sh
voldemort . --django-settings project.settings \
  --django-template shop/templates \
  --runtime-evidence .voldemort-calls.json --explain
```

An observation retains only the exact function, method, or property that ran,
matched by project-relative path, Python qualified name, declaration line, and
source hash. Changed source and unmatched observations produce coverage notes.
Unused parameters and locals remain reportable. Runtime evidence also works
with `--no-django`.

Recording stores call identities and source hashes, without argument values or
locals. It covers the recorder's Python process; child processes require their
own recordings. Unobserved code receives no extra exemption, and absence from
a recording does not prove that code is dead. Normal analysis only reads the
recording and never executes the project.

## Configuration

```toml
[tool.voldemort]
paths = ["project", "shop", "tests"]
project_root = "."
exclude = ["*/.venv/*"]
min_confidence = 0
explain = false
# Optional: combine call recordings without executing the project.
# runtime_evidence = [".voldemort-calls.json"]

[tool.voldemort.django]
enabled = true
settings = "project.settings"
# Optional: scan template references.
# templates = ["shop/templates"]
# Optional: override the settings module's installed app list.
# apps = ["shop"]
```

Command-line options override configuration. Use `--config path/to/pyproject.toml`
to select another file. `voldemort` reads `[tool.voldemort]`; the retained `vulture`
command continues to read `[tool.vulture]`.

Voldemort also supports Vulture's `--ignore-names`, `--ignore-decorators`,
`--make-whitelist`, `--sort-by-size`, and `--min-confidence` flags. Use
`--no-django` for Vulture's original analysis. See `voldemort --help` and
[the upstream Vulture documentation](VULTURE.md) for general behavior.

## Python API

```python
from voldemort import Voldemort

analyzer = Voldemort(
    django_settings="project.settings",
    django_templates=["shop/templates"],
    runtime_evidence=[".voldemort-calls.json"],
    project_root=".",
)
analyzer.scavenge(["project", "shop", "tests"])
findings = analyzer.get_unused_code()
evidence = analyzer.django_evidence
notes = analyzer.django_notes
observed = analyzer.runtime_evidence
runtime_notes = analyzer.runtime_notes
```

## Limits and next steps

The initial contract catalog targets Django 5.2. Unsupported, dynamic, or
unresolved declarations receive no broad Django exemption. Vulture still
compares ordinary Python names globally, so its existing false positives and
false negatives remain possible.

Assignments that remove inherited form fields with `None` can still be reported
as unused; the corresponding field cleaners remain reportable.

Third-party extensions and unsupported template syntax remain outside the static
contracts. Dynamic registration, computed settings, unscanned bases, and arbitrary
import side effects cannot be fully resolved. Migration references to historical model
state are not treated as usage of today's model declarations. This analysis
recognizes framework references; it does not prove complete runtime reachability.
Runtime evidence supplements these contracts with observed calls; it does not
establish complete runtime coverage.

## Development

```sh
python -m venv .venv
.venv/bin/python -m pip install -e . pytest pytest-cov pint ruff build
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m build
```

Voldemort is an MIT-licensed fork of Vulture by Jendrik Seipp and contributors.
The upstream engine and `vulture` Python API are retained.
