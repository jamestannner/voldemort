from textwrap import dedent

import pytest

from voldemort import Voldemort
from vulture import Vulture


def scan(source, **kwargs):
    scanner = Voldemort(**kwargs)
    scanner.scan(dedent(source), filename="example.py")
    return scanner


def names(items):
    return {item.name for item in items}


def locations(items):
    return {(item.filename, item.first_lineno, item.name) for item in items}


def test_model_hooks_are_scoped_to_their_definitions():
    scanner = scan(
        """\
        from django.db import models

        class Book(models.Model):
            title = models.CharField(max_length=200)

            def clean(self):
                spare = 42

        class Ordinary:
            title = 42

            def clean(self):
                pass
        """
    )

    assert names(scanner.unused_classes) == {"Book", "Ordinary"}
    assert {
        (item.name, item.first_lineno) for item in scanner.unused_methods
    } == {("clean", 12)}
    assert {
        (item.name, item.first_lineno) for item in scanner.unused_vars
    } == {
        ("spare", 7),
        ("title", 10),
    }
    assert "clean" not in scanner.used_names
    assert "title" not in scanner.used_names


@pytest.mark.parametrize(
    ("imports", "base", "field"),
    [
        (
            "import django.db.models as orm",
            "orm.Model",
            "orm.CharField",
        ),
        (
            "from django.db.models import Model as Base, CharField as Text",
            "Base",
            "Text",
        ),
        (
            "from django.db import models as orm",
            "orm.Model",
            "orm.CharField",
        ),
    ],
)
def test_model_import_aliases(imports, base, field):
    scanner = scan(
        f"""\
        {imports}

        class Book({base}):
            title = {field}(max_length=200)

            def save(self):
                pass
        """
    )

    assert "save" not in names(scanner.unused_methods)
    assert "title" not in names(scanner.unused_vars)


def test_model_hooks_include_inherited_plain_mixins():
    scanner = scan(
        """\
        from django.db import models

        class ValidationMixin:
            def clean(self):
                pass

        class Book(ValidationMixin, models.Model):
            pass

        class Ordinary:
            def clean(self):
                pass
        """
    )

    assert {
        (item.name, item.first_lineno) for item in scanner.unused_methods
    } == {("clean", 11)}


def test_overridden_plain_mixin_hook_remains_reportable():
    scanner = scan(
        """\
        from django.db import models

        class ValidationMixin:
            def clean(self):
                pass

        class Book(ValidationMixin, models.Model):
            def clean(self):
                pass
        """
    )

    assert {
        (item.name, item.first_lineno) for item in scanner.unused_methods
    } == {("clean", 4)}


@pytest.mark.parametrize("child_first", [False, True])
def test_cross_module_inheritance_is_independent_of_scan_order(
    tmp_path, child_first
):
    scanner = Voldemort(project_root=tmp_path)
    modules = [
        (
            tmp_path / "base.py",
            """\
            from django import forms

            class BaseForm(forms.Form):
                title = forms.CharField()
            """,
        ),
        (
            tmp_path / "child.py",
            """\
            from base import BaseForm as Parent

            class BookForm(Parent):
                def clean_title(self):
                    pass

                def clean_typo(self):
                    pass
            """,
        ),
    ]
    if child_first:
        modules.reverse()
    for filename, source in modules:
        scanner.scan(dedent(source), filename=filename)
        # Reading the result must not freeze evidence before later scans.
        scanner.get_unused_code()

    assert names(scanner.unused_methods) == {"clean_typo"}
    assert "title" not in names(scanner.unused_vars)
    assert "BookForm" in names(scanner.unused_classes)


def test_form_cleaners_require_an_existing_field():
    scanner = scan(
        """\
        from django import forms as f

        class BookForm(f.Form):
            title = f.CharField()

            def clean(self):
                pass

            def clean_title(self):
                pass

            def clean_typo(self):
                pass

        class Ordinary:
            title = 42

            def clean_title(self):
                pass
        """
    )

    assert {
        (item.name, item.first_lineno) for item in scanner.unused_methods
    } == {
        ("clean_typo", 12),
        ("clean_title", 18),
    }
    assert names(scanner.unused_classes) == {"BookForm", "Ordinary"}


def test_form_field_cleaner_from_a_plain_mixin():
    scanner = scan(
        """\
        from django import forms

        class TitleMixin:
            def clean_title(self):
                pass

        class BookForm(TitleMixin, forms.Form):
            title = forms.CharField()
        """
    )

    assert "title" not in names(scanner.unused_vars)
    assert "clean_title" not in names(scanner.unused_methods)
    assert "BookForm" in names(scanner.unused_classes)


def test_form_does_not_collect_fields_from_plain_mixins():
    scanner = scan(
        """\
        from django import forms

        class TitleMixin:
            title = forms.CharField()

        class BookForm(TitleMixin, forms.Form):
            def clean_title(self):
                pass
        """
    )

    assert names(scanner.unused_vars) == {"title"}
    assert names(scanner.unused_methods) == {"clean_title"}


def test_form_field_can_be_removed_by_a_subclass():
    scanner = scan(
        """\
        from django import forms

        class BaseForm(forms.Form):
            title = forms.CharField()

        class BookForm(BaseForm):
            title = None

            def clean_title(self):
                pass
        """
    )

    assert names(scanner.unused_methods) == {"clean_title"}


def test_plain_form_meta_is_not_a_framework_contract():
    scanner = scan(
        """\
        from django import forms

        class BookForm(forms.Form):
            class Meta:
                fields = ["title"]
        """
    )

    assert names(scanner.unused_classes) == {"BookForm", "Meta"}
    assert names(scanner.unused_vars) == {"fields"}


def test_model_fields_do_not_make_model_field_cleaners_into_hooks():
    scanner = scan(
        """\
        from django.db import models

        class Book(models.Model):
            title = models.CharField(max_length=200)

            def clean_title(self):
                pass
        """
    )

    assert names(scanner.unused_methods) == {"clean_title"}


def test_custom_field_subclasses_are_resolved_without_suffix_guessing():
    scanner = scan(
        """\
        from django.db import models

        class CustomText(models.CharField):
            pass

        class FakeField:
            pass

        class Book(models.Model):
            title = CustomText(max_length=200)
            fake = FakeField()
            imaginary = models.ImaginaryField()
        """
    )

    assert names(scanner.unused_vars) == {"fake", "imaginary"}


def test_unrelated_field_declarations_are_not_exempted():
    scanner = scan(
        """\
        from django import forms

        class Ordinary:
            title = forms.CharField()
        """
    )

    assert names(scanner.unused_vars) == {"title"}


def test_model_meta_only_exempts_known_options():
    scanner = scan(
        """\
        from django.db import models

        class Book(models.Model):
            class Meta:
                db_table = "books"
                verbose_name = "book"
                db_tabel = "typo"

        class Ordinary:
            class Meta:
                db_table = "ordinary"
        """
    )

    assert names(scanner.unused_vars) == {"db_table", "db_tabel"}
    assert {
        (item.name, item.first_lineno) for item in scanner.unused_classes
    } == {
        ("Book", 3),
        ("Ordinary", 9),
        ("Meta", 10),
    }


@pytest.mark.parametrize(
    "selection", ['fields = ["title"]', 'fields = "__all__"']
)
def test_modelform_cleaners_for_generated_model_fields(selection):
    scanner = scan(
        f"""\
        from django.db import models
        from django import forms

        class Book(models.Model):
            title = models.CharField(max_length=200)

        class BookForm(forms.ModelForm):
            class Meta:
                model = Book
                {selection}

            def clean_title(self):
                pass

            def clean_typo(self):
                pass
        """
    )

    assert names(scanner.unused_methods) == {"clean_typo"}
    assert names(scanner.unused_classes) == {"BookForm"}
    assert not names(scanner.unused_vars)


def test_modelform_does_not_exempt_cleaners_for_excluded_fields():
    scanner = scan(
        """\
        from django.db import models
        from django import forms

        class Book(models.Model):
            title = models.CharField(max_length=200)

        class BookForm(forms.ModelForm):
            class Meta:
                model = Book
                exclude = ["title"]

            def clean_title(self):
                pass
        """
    )

    assert names(scanner.unused_methods) == {"clean_title"}


@pytest.mark.parametrize(
    ("field_name", "declaration"),
    [
        ("id", "models.AutoField(primary_key=True)"),
        ("blob", "models.BinaryField()"),
        (
            "computed",
            "models.GeneratedField(expression=models.F('title'), "
            "output_field=models.IntegerField(), db_persist=True)",
        ),
        ("content_object", "GenericForeignKey()"),
        ("tags", "GenericRelation('tags.Tag')"),
        ("pk", "models.CompositePrimaryKey('title', 'other')"),
    ],
)
def test_modelform_does_not_generate_cleaners_for_nonform_model_fields(
    field_name, declaration
):
    scanner = scan(
        f"""\
        from django.db import models
        from django import forms
        from django.contrib.contenttypes.fields import (
            GenericForeignKey, GenericRelation,
        )

        class Book(models.Model):
            title = models.CharField(max_length=200)
            other = models.CharField(max_length=200)
            {field_name} = {declaration}

        class BookForm(forms.ModelForm):
            class Meta:
                model = Book
                fields = "__all__"

            def clean_{field_name}(self):
                pass

            def clean_title(self):
                pass
        """
    )

    assert names(scanner.unused_methods) == {f"clean_{field_name}"}


def test_modelform_can_generate_explicitly_editable_binary_field():
    scanner = scan(
        """\
        from django.db import models
        from django import forms

        class Book(models.Model):
            blob = models.BinaryField(editable=True)

        class BookForm(forms.ModelForm):
            class Meta:
                model = Book
                fields = ["blob"]

            def clean_blob(self):
                pass
        """
    )

    assert not scanner.unused_methods


def test_conditional_form_field_removal_keeps_cleaner_reportable():
    scanner = scan(
        """\
        from django import forms

        class BookForm(forms.Form):
            title = forms.CharField()

            if UNKNOWN:
                title = None

            def clean_title(self):
                pass

            def clean(self):
                pass
        """
    )

    assert names(scanner.unused_methods) == {"clean_title"}


def test_modelform_uses_inherited_model_fields():
    scanner = scan(
        """\
        from django.db import models
        from django import forms

        class Titled(models.Model):
            title = models.CharField(max_length=200)

            class Meta:
                abstract = True

        class Book(Titled):
            pass

        class BookForm(forms.ModelForm):
            class Meta:
                model = Book
                fields = ["title"]

            def clean_title(self):
                pass
        """
    )

    assert not scanner.unused_methods


def test_unknown_modelform_model_keeps_field_cleaners_reportable():
    scanner = scan(
        """\
        from django import forms
        from external import Book

        class BookForm(forms.ModelForm):
            class Meta:
                model = Book
                fields = ["title"]

            def clean_title(self):
                pass
        """
    )

    assert names(scanner.unused_methods) == {"clean_title"}


def test_configured_app_discovers_its_models_module(tmp_path):
    scanner = Voldemort(django_apps=["shop"], project_root=tmp_path)
    scanner.scan(
        "from django.db import models\nclass Book(models.Model):\n    pass\n",
        filename=tmp_path / "shop" / "models.py",
    )
    scanner.scan(
        "from django.db import models\nclass Spare(models.Model):\n    pass\n",
        filename=tmp_path / "shop" / "models_extra.py",
    )

    assert names(scanner.unused_classes) == {"Spare"}


def test_configured_app_does_not_register_unused_abstract_models(tmp_path):
    scanner = Voldemort(django_apps=["shop"], project_root=tmp_path)
    scanner.scan(
        dedent(
            """\
            from django.db import models

            class AbstractBook(models.Model):
                class Meta:
                    abstract = True

            class Book(models.Model):
                pass
            """
        ),
        filename=tmp_path / "shop" / "models.py",
    )

    assert names(scanner.unused_classes) == {"AbstractBook"}


def test_registration_respects_inherited_meta_abstract_flag(tmp_path):
    scanner = Voldemort(django_apps=["shop"], project_root=tmp_path)
    scanner.scan(
        dedent(
            """\
            from django.db import models

            class Flags:
                abstract = True

            class AbstractByFlags(models.Model):
                class Meta(Flags):
                    pass

            class AbstractBook(models.Model):
                class Meta:
                    abstract = True

            class Book(AbstractBook):
                pass
            """
        ),
        filename=tmp_path / "shop" / "models.py",
    )

    assert names(scanner.unused_classes) == {"AbstractByFlags"}


@pytest.mark.parametrize("child_first", [False, True])
def test_configured_models_package_follows_explicit_imports(
    tmp_path, child_first
):
    scanner = Voldemort(django_apps=["shop"], project_root=tmp_path)
    modules = [
        (
            tmp_path / "shop" / "models" / "__init__.py",
            "from . import books\n",
        ),
        (
            tmp_path / "shop" / "models" / "books.py",
            "from django.db import models\n"
            "class Book(models.Model):\n    pass\n",
        ),
        (
            tmp_path / "shop" / "models" / "spare.py",
            "from django.db import models\n"
            "class Spare(models.Model):\n    pass\n",
        ),
    ]
    if child_first:
        modules.reverse()
    for filename, source in modules:
        scanner.scan(source, filename=filename)

    assert names(scanner.unused_classes) == {"Spare"}


@pytest.mark.parametrize(
    "exports",
    [
        "spare = 42\n",
        "spare = compute()\n",
        "def __getattr__(name):\n    return 42\n",
    ],
)
def test_imported_package_attribute_does_not_load_same_named_submodule(
    tmp_path, exports
):
    scanner = Voldemort(django_apps=["shop"], project_root=tmp_path)
    scanner.scan(
        "from .helpers import spare\n",
        filename=tmp_path / "shop" / "models.py",
    )
    scanner.scan(
        exports,
        filename=tmp_path / "shop" / "helpers" / "__init__.py",
    )
    scanner.scan(
        "from django.db import models\n"
        "class Orphan(models.Model):\n    pass\n",
        filename=tmp_path / "shop" / "helpers" / "spare.py",
    )

    assert names(scanner.unused_classes) == {"Orphan"}


def test_model_registration_follows_executed_class_body_imports(tmp_path):
    scanner = Voldemort(django_apps=["shop"], project_root=tmp_path)
    scanner.scan(
        "from django.db import models\n"
        "class Book(models.Model):\n"
        "    from . import extra\n",
        filename=tmp_path / "shop" / "models.py",
    )
    scanner.scan(
        "from django.db import models\n"
        "class Additional(models.Model):\n    pass\n",
        filename=tmp_path / "shop" / "extra.py",
    )

    assert not scanner.unused_classes


def test_scavenge_preserves_exclusions_and_never_executes_project(tmp_path):
    app = tmp_path / "shop"
    app.mkdir()
    (app / "__init__.py").write_text(
        'raise RuntimeError("project code must not run")\n'
    )
    (app / "models.py").write_text(
        "from django.db import models\n"
        "class Book(models.Model):\n"
        "    title = models.CharField(max_length=200)\n"
        "    def clean(self):\n"
        "        spare = 42\n"
    )
    (app / "excluded.py").write_text("excluded_variable = 42\n")
    scanner = Voldemort(django_apps=["shop"], project_root=tmp_path)

    scanner.scavenge([app], exclude=["excluded.py"])

    assert not scanner.unused_classes
    assert not scanner.unused_methods
    assert names(scanner.unused_vars) == {"spare"}


def test_exempted_methods_still_report_local_and_nested_dead_code():
    scanner = scan(
        """\
        from django.db import models

        class Book(models.Model):
            def save(self, unused_argument):
                import unused_module
                spare = 42

                def clean():
                    pass

                return
                print("unreachable")
        """
    )

    assert not scanner.unused_methods
    assert names(scanner.unused_funcs) == {"clean"}
    assert names(scanner.unused_imports) == {"unused_module"}
    assert names(scanner.unused_vars) == {"spare", "unused_argument"}
    assert len(scanner.unreachable_code) == 1
    assert scanner.unreachable_code[0] in scanner.get_unused_code()


def test_disabled_django_analysis_matches_vulture():
    source = dedent(
        """\
        from django.db import models

        class Book(models.Model):
            title = models.CharField(max_length=200)

            def clean(self):
                spare = 42
        """
    )
    voldemort = Voldemort(django_enabled=False)
    vulture = Vulture()
    for scanner in (voldemort, vulture):
        scanner.scan(source, filename="example.py")

    assert locations(voldemort.get_unused_code()) == locations(
        vulture.get_unused_code()
    )
    assert not voldemort.django_evidence


def test_evidence_explains_only_the_scoped_exemptions():
    scanner = scan(
        """\
        from django.db import models

        class Book(models.Model):
            def clean(self):
                spare = 42

        class Ordinary:
            def clean(self):
                pass
        """
    )

    evidence = [
        entry for entry in scanner.django_evidence if entry.name == "clean"
    ]
    assert len(evidence) == 1
    entry = evidence[0]
    assert str(entry.filename) == "example.py"
    assert entry.lineno == 4
    assert entry.qualified_name.endswith("Book.clean")
    assert entry.rule
    assert entry.reason
    assert not any(entry.name == "spare" for entry in scanner.django_evidence)


def test_unresolved_base_is_not_assumed_to_be_django():
    scanner = scan(
        """\
        from unknown import Model

        class Book(Model):
            def clean(self):
                pass
        """
    )

    assert names(scanner.unused_methods) == {"clean"}
    assert not scanner.django_evidence


@pytest.mark.parametrize(
    "shadow",
    [
        "match value:\n    case models:\n        pass\n",
        "match value:\n    case [*models]:\n        pass\n",
        "match value:\n    case {**models}:\n        pass\n",
        "try:\n    pass\nexcept RuntimeError as models:\n    pass\n",
    ],
)
def test_pattern_and_exception_captures_shadow_django_imports(shadow):
    scanner = scan(
        "from django.db import models\n"
        + shadow
        + "\nclass Book(models.Model):\n"
        "    def clean(self):\n        pass\n"
    )

    assert names(scanner.unused_methods) == {"clean"}
    assert not scanner.django_evidence


def test_model_hook_named_function_outside_class_is_reported():
    scanner = scan(
        """\
        from django.db import models

        class Book(models.Model):
            def save(self):
                pass

        def save():
            pass
        """
    )

    assert not scanner.unused_methods
    assert names(scanner.unused_funcs) == {"save"}
