import ast
from textwrap import dedent
from types import SimpleNamespace

import pytest

from voldemort.django import _Index
from voldemort.templates import run


def analyze(
    tmp_path, sources, templates, *, apps=("shop",), settings="", exclude=()
):
    modules = {
        tmp_path.joinpath(*name.split(".")).with_suffix(".py"): ast.parse(
            dedent(source)
        )
        for name, source in sources.items()
    }
    directory = tmp_path / "templates"
    for name, text in templates.items():
        filename = directory / name
        filename.parent.mkdir(parents=True, exist_ok=True)
        filename.write_text(text)
    index = _Index(modules, tmp_path)
    discovery = SimpleNamespace(
        apps=list(apps),
        loaded_modules=set(sources),
        url_modules={"project.urls"},
        settings_module=settings,
    )
    run(index, discovery, [directory], exclude=exclude)
    return index.result


def retained(result, rule=None):
    return {
        entry.qualified_name
        for entry in result.evidence.values()
        if rule is None or rule == entry.rule
    }


LIBRARY = """\
    from django import template as django_template
    register = django_template.Library()

    @register.filter(name='pretty')
    def presentation(value):
        return value

    @register.simple_tag(name='badge')
    def decorated(value):
        return value

    @register.filter
    def unused_filter(value):
        return value

    def unrelated():
        return None
"""


def test_loaded_library_retains_only_used_callbacks(tmp_path):
    result = analyze(
        tmp_path,
        {
            "shop.templatetags.catalog": LIBRARY,
            "ordinary": "def presentation(value): return value\n"
            "def decorated(value): return value",
        },
        {"detail.html": "{% load catalog %}{{ book|pretty }}{% badge book %}"},
    )
    assert retained(result) == {
        "shop.templatetags.catalog.register",
        "shop.templatetags.catalog.presentation",
        "shop.templatetags.catalog.decorated",
    }


@pytest.mark.parametrize(
    "registration",
    [
        "@register.filter\ndef actual(value): return value",
        "@register.filter()\ndef actual(value): return value",
        "@register.filter('actual')\ndef named(value): return value",
        "@register.filter(name='actual')\ndef named(value): return value",
        "def named(value): return value\nregister.filter('actual', named)",
        "def actual(value): return value\nregister.filter(actual)",
        "def named(value): return value\n"
        "register.filter(name='actual', filter_func=named)",
    ],
)
def test_filter_registration_forms(tmp_path, registration):
    result = analyze(
        tmp_path,
        {
            "shop.templatetags.catalog": "from django.template "
            "import Library\n"
            "register = Library()\n" + registration
        },
        {"detail.html": "{% load catalog %}{{ value|actual }}"},
    )
    assert len(retained(result, "django.template-filter")) == 1


@pytest.mark.parametrize(
    "registration",
    [
        "@register.tag\ndef actual(parser, token): pass",
        "@register.tag('actual')\ndef named(parser, token): pass",
        "def named(parser, token): pass\nregister.tag('actual', named)",
        "@register.simple_tag(name='actual')\ndef named(): pass",
        "@register.inclusion_tag('fragment.html', name='actual')\n"
        "def named(): return {}",
        "@register.simple_block_tag(name='actual')\ndef named(content): pass",
        "def named(): pass\nregister.simple_tag(named, name='actual')",
    ],
)
def test_tag_registration_forms(tmp_path, registration):
    result = analyze(
        tmp_path,
        {
            "shop.templatetags.catalog": "from django.template.library "
            "import Library as Registry\nregister = Registry()\n"
            + registration
        },
        {"detail.html": "{% load catalog %}{% actual %}"},
    )
    assert len(retained(result, "django.template-tag")) == 1


def test_library_aliases_and_final_export_choose_correct_instance(tmp_path):
    result = analyze(
        tmp_path,
        {
            "shop.templatetags.catalog": """\
                from django.template import Library as Registry
                old = Registry()
                register = old
                @register.filter(name='pretty')
                def stale(value): return value
                lib = Registry()
                alias = lib
                @alias.filter(name='pretty')
                def current(value): return value
                register = lib
            """,
        },
        {"detail.html": "{% load catalog %}{{ value|pretty }}"},
    )
    assert retained(result, "django.template-filter") == {
        "shop.templatetags.catalog.current"
    }


def test_last_registration_wins_and_redefined_function_is_not_confused(
    tmp_path,
):
    result = analyze(
        tmp_path,
        {
            "shop.templatetags.catalog": """\
                from django.template import Library
                register = Library()
                @register.filter(name='pretty')
                def stale(value): return value
                @register.filter(name='pretty')
                def callback(value): return value
                def callback(value): return None
            """,
        },
        {"detail.html": "{% load catalog %}{{ value|pretty }}"},
    )
    entries = [
        entry
        for entry in result.evidence.values()
        if entry.rule == "django.template-filter"
    ]
    assert len(entries) == 1
    assert entries[0].lineno == 5


def test_load_from_selects_names(tmp_path):
    result = analyze(
        tmp_path,
        {"shop.templatetags.catalog": LIBRARY},
        {
            "detail.html": "{% load pretty from catalog %}"
            "{{ book|pretty }}{% badge book %}"
        },
    )
    assert retained(result, "django.template-filter") == {
        "shop.templatetags.catalog.presentation"
    }
    assert retained(result, "django.template-tag") == set()


@pytest.mark.parametrize(
    "text",
    [
        "{# {% load catalog %}{{ book|pretty }} #}",
        "{% comment %}{% load catalog %}{{ book|pretty }}{% endcomment %}",
        "{% verbatim %}{% load catalog %}{{ book|pretty }}{% endverbatim %}",
        "{% verbatim raw %}{% load catalog %}{{ book|pretty }}"
        "{% endverbatim raw %}",
        "{% load catalog %}{{ 'literal|pretty' }}",
        "{% load catalog %}{% if value == 'literal|pretty' %}{% endif %}",
    ],
)
def test_comments_verbatim_and_quoted_pipes_are_ignored(tmp_path, text):
    result = analyze(
        tmp_path, {"shop.templatetags.catalog": LIBRARY}, {"detail.html": text}
    )
    assert retained(result, "django.template-filter") == set()


def test_lookalike_library_and_dynamic_name_produce_no_exemption(tmp_path):
    result = analyze(
        tmp_path,
        {
            "shop.templatetags.catalog": """\
            from unrelated import Library
            register = Library()
            @register.filter(name='pretty')
            def presentation(value): return value
        """
        },
        {"detail.html": "{% load catalog %}{{ value|pretty }}"},
    )
    assert retained(result) == set()
    assert result.notes


def test_configured_alias_and_builtin_libraries(tmp_path):
    result = analyze(
        tmp_path,
        {
            "extras.catalog": LIBRARY,
            "extras.builtin": "from django.template import Library\n"
            "register=Library()\n@register.filter\n"
            "def built(value): return value",
            "project.settings": "TEMPLATES=[{'BACKEND':"
            "'django.template.backends.django.DjangoTemplates',"
            "'OPTIONS':{'libraries':{'renamed':'extras.catalog'},"
            "'builtins':['extras.builtin']}}]",
        },
        {"detail.html": "{% load renamed %}{{ value|pretty|built }}"},
        settings="project.settings",
    )
    assert retained(result, "django.template-filter") == {
        "extras.catalog.presentation",
        "extras.builtin.built",
    }


def test_ambiguous_app_library_has_no_guessed_owner(tmp_path):
    result = analyze(
        tmp_path,
        {
            "shop.templatetags.catalog": LIBRARY,
            "other.templatetags.catalog": LIBRARY,
        },
        {"detail.html": "{% load catalog %}{{ value|pretty }}"},
        apps=("shop", "other"),
    )
    assert retained(result) == set()
    assert any("Ambiguous" in note for note in result.notes)


MODELS = """\
    from django.db import models
    class Book(models.Model):
        def label(self): return 'book'
        @property
        def title(self): return 'book'
        def never_used(self): return None
    class Ordinary:
        def label(self): return 'ordinary'
        @property
        def title(self): return 'ordinary'
"""


def view_sources(view="DetailView", attributes="", methods=""):
    return {
        "shop.models": MODELS,
        "shop.views": f"from django.views.generic import {view}\n"
        f"from .models import Book\nclass Books({view}):\n"
        "    model = Book\n    template_name = 'detail.html'\n"
        + attributes
        + methods,
        "project.urls": "from shop.views import Books\n"
        "urlpatterns = [Books.as_view()]",
    }


def test_detail_view_context_is_definition_specific(tmp_path):
    result = analyze(
        tmp_path,
        view_sources(),
        {
            "detail.html": "{{ object.label }}{{ book.title }}"
            "{{ unknown.never_used }}"
        },
    )
    assert retained(result, "django.template-context") == {
        "shop.models.Book.label",
        "shop.models.Book.title",
    }


def test_list_view_loop_and_named_context(tmp_path):
    result = analyze(
        tmp_path,
        view_sources("ListView", "    context_object_name = 'books'\n"),
        {
            "detail.html": "{% for book in books %}{{ book.label }}"
            "{% endfor %}{{ book.title }}"
        },
    )
    assert retained(result, "django.template-context") == {
        "shop.models.Book.label"
    }


@pytest.mark.parametrize(
    "method",
    [
        "get_context_data",
        "get_queryset",
        "get_object",
        "get_context_object_name",
        "get_template_names",
        "get",
        "dispatch",
        "setup",
        "render_to_response",
    ],
)
def test_custom_view_context_methods_cancel_inference(tmp_path, method):
    result = analyze(
        tmp_path,
        view_sources(methods=f"    def {method}(self): return None\n"),
        {"detail.html": "{{ book.label }}"},
    )
    assert retained(result, "django.template-context") == set()
    assert any("cannot be inferred" in note for note in result.notes)


def test_untyped_context_never_exempts_matching_names(tmp_path):
    result = analyze(
        tmp_path,
        {"shop.models": MODELS},
        {"detail.html": "{{ book.label }}{{ object.title }}"},
    )
    assert retained(result) == set()
    assert any("Untyped" in note for note in result.notes)


def test_literal_render_with_exact_model_instance_and_local_alias(tmp_path):
    sources = {
        "shop.models": MODELS,
        "shop.views": """\
        from django.shortcuts import render as respond
        from .models import Book
        def detail(request):
            book = Book()
            alias = book
            return respond(request, 'detail.html', {'selected': alias})
    """,
    }
    result = analyze(
        tmp_path,
        sources,
        {"detail.html": "{{ selected.label }}{{ book.title }}"},
    )
    assert retained(result, "django.template-context") == {
        "shop.models.Book.label"
    }


def test_render_local_alias_reassigned_and_parameters_shadow_imports(tmp_path):
    sources = {
        "shop.models": MODELS,
        "shop.views": """\
        from django.shortcuts import render
        from .models import Book
        def detail(request, Book):
            book = Book()
            return render(request, 'detail.html', {'book': book})
        def detail2(request):
            book = Book()
            book = unknown()
            return render(request, 'detail.html', {'book': book})
    """,
    }
    result = analyze(tmp_path, sources, {"detail.html": "{{ book.label }}"})
    assert retained(result, "django.template-context") == set()


def test_include_extends_and_with_alias_preserve_typed_context(tmp_path):
    result = analyze(
        tmp_path,
        view_sources(),
        {
            "detail.html": "{% extends 'base.html' %}{% block content %}"
            "{% with book as selected %}"
            "{% include 'fragment.html' with chosen=selected only %}"
            "{% endwith %}{% endblock %}",
            "base.html": "{{ object.title }}{% block content %}{% endblock %}",
            "fragment.html": "{{ chosen.label }}",
        },
    )
    assert retained(result, "django.template-context") == {
        "shop.models.Book.title",
        "shop.models.Book.label",
    }


def test_include_only_and_shadowed_with_loop_vars_are_untyped(tmp_path):
    result = analyze(
        tmp_path,
        view_sources(),
        {
            "detail.html": "{% include 'fragment.html' only %}"
            "{% with book=unknown %}{{ book.label }}{% endwith %}"
            "{% for book in unknown %}{{ book.label }}"
            "{% empty %}{{ book.title }}{% endfor %}",
            "fragment.html": "{{ book.label }}",
        },
    )
    assert retained(result, "django.template-context") == {
        "shop.models.Book.title"
    }


def test_include_has_separate_loaded_library_scope(tmp_path):
    result = analyze(
        tmp_path,
        {"shop.templatetags.catalog": LIBRARY},
        {
            "detail.html": "{% load catalog %}{% include 'fragment.html' %}",
            "fragment.html": "{{ value|pretty }}",
        },
    )
    assert retained(result, "django.template-filter") == set()


def test_extra_context_override_drops_model_binding(tmp_path):
    result = analyze(
        tmp_path,
        view_sources(attributes="    extra_context = {'book': 'different'}\n"),
        {"detail.html": "{{ book.label }}{{ object.title }}"},
    )
    assert retained(result, "django.template-context") == {
        "shop.models.Book.title"
    }


def test_node_render_reachable_from_used_tag(tmp_path):
    result = analyze(
        tmp_path,
        {
            "shop.templatetags.catalog": """\
            from django.template import Library, Node
            register = Library()
            class Callback(Node):
                def render(self, context): return 'hello'
                def helper(self): return 'unused'
            @register.tag(name='greeting')
            def compile_greeting(parser, token): return Callback()
        """
        },
        {"detail.html": "{% load catalog %}{% greeting %}"},
    )
    assert retained(result, "django.template-node") == {
        "shop.templatetags.catalog.Callback.render"
    }


def test_missing_paths_explicit_other_extensions_and_excludes(tmp_path):
    index = _Index({}, tmp_path)
    discovery = SimpleNamespace(
        apps=[], settings_module="", loaded_modules=set(), url_modules=set()
    )
    run(index, discovery, [tmp_path / "missing"])
    assert any("does not exist" in note for note in index.result.notes)
    sources = {"shop.templatetags.catalog": LIBRARY}
    result = analyze(
        tmp_path,
        sources,
        {"excluded/detail.html": "{% load catalog %}{{ value|pretty }}"},
        exclude=["*excluded*"],
    )
    assert retained(result) == set()


def test_dynamic_include_and_cycle_are_bounded(tmp_path):
    result = analyze(
        tmp_path,
        {},
        {
            "detail.html": "{% include dynamic_name %}"
            "{% include 'detail.html' %}"
        },
    )
    assert any("Dynamic" in note for note in result.notes)
    assert any("Cyclic" in note for note in result.notes)


@pytest.mark.parametrize(
    "attribute",
    [
        "queryset = unknown",
        "queryset = Book.objects.all()",
        "extra_context = unknown",
        "extra_context = make_context()",
    ],
)
def test_unknown_queryset_and_extra_context_cancel_typing(tmp_path, attribute):
    result = analyze(
        tmp_path,
        view_sources(attributes=f"    {attribute}\n"),
        {"detail.html": "{{ book.label }}{{ object.title }}"},
    )
    assert retained(result, "django.template-context") == set()


def test_unknown_context_name_does_not_guess_model_alias(tmp_path):
    result = analyze(
        tmp_path,
        view_sources(attributes="    context_object_name = unknown\n"),
        {"detail.html": "{{ book.label }}{{ object.title }}"},
    )
    assert retained(result, "django.template-context") == {
        "shop.models.Book.title"
    }


@pytest.mark.parametrize(
    "overrides",
    [
        "queryset=unknown",
        "extra_context=unknown",
        "model=unknown",
        "model=None",
        "**unknown",
    ],
)
def test_as_view_unknown_overrides_cancel_typing(tmp_path, overrides):
    sources = view_sources()
    sources["project.urls"] = (
        "from shop.views import Books\n"
        + f"urlpatterns = [Books.as_view({overrides})]"
    )
    result = analyze(tmp_path, sources, {"detail.html": "{{ book.label }}"})
    assert retained(result, "django.template-context") == set()


def test_unresolved_view_mixin_does_not_guess_context(tmp_path):
    sources = view_sources()
    sources["shop.views"] = (
        "from unknown import Mixin\n"
        "from django.views.generic import DetailView\n"
        "class Books(Mixin, DetailView): pass"
    )
    sources["project.urls"] = (
        "from shop.views import Books\nfrom shop.models import Book\n"
        "urlpatterns=[Books.as_view(model=Book, "
        "template_name='detail.html')]"
    )
    result = analyze(tmp_path, sources, {"detail.html": "{{ book.label }}"})
    assert retained(result, "django.template-context") == set()


def test_class_literal_alias_is_not_resolved_from_later_globals(tmp_path):
    sources = view_sources()
    sources["shop.views"] = """\
        from django.views.generic import DetailView
        from .models import Book
        NAME = 'actual.html'
        class Books(DetailView):
            model = Book
            template_name = NAME
        NAME = 'detail.html'
    """
    result = analyze(tmp_path, sources, {"detail.html": "{{ book.label }}"})
    assert retained(result, "django.template-context") == set()


@pytest.mark.parametrize("suffix", ["render = unknown", "Book = unknown"])
def test_function_locals_shadow_globals_before_assignment(tmp_path, suffix):
    sources = {
        "shop.models": MODELS,
        "shop.views": "from django.shortcuts import render\n"
        "from .models import Book\ndef view(request):\n"
        "    result = render(request, 'detail.html', {'book': Book()})\n    "
        + suffix,
    }
    result = analyze(tmp_path, sources, {"detail.html": "{{ book.label }}"})
    assert retained(result, "django.template-context") == set()


def test_function_uses_current_module_globals_and_duplicate_context_keys(
    tmp_path,
):
    result = analyze(
        tmp_path,
        {
            "shop.models": MODELS,
            "shop.views": """\
        from django.shortcuts import render
        from .models import Book
        def view(request):
            return render(request, 'detail.html', {'book': Book()})
        Book = unrelated
    """,
        },
        {"detail.html": "{{ book.label }}"},
    )
    assert retained(result, "django.template-context") == set()
    result = analyze(
        tmp_path,
        {
            "shop.models": MODELS,
            "shop.views": """\
        from django.shortcuts import render
        from .models import Book
        def view(request):
            return render(request, 'detail.html', {
                'book': Book(), 'book': unknown,
            })
    """,
        },
        {"detail.html": "{{ book.label }}"},
    )
    assert retained(result, "django.template-context") == set()


def test_raw_tag_and_literal_block_name_do_not_imply_model_lookup(tmp_path):
    sources = view_sources()
    sources["shop.templatetags.catalog"] = (
        "from django.template import Library\nregister=Library()\n"
        "@register.tag\ndef raw(parser, token): pass"
    )
    result = analyze(
        tmp_path,
        sources,
        {
            "detail.html": "{% load catalog %}{% raw book.label %}"
            "{% block book.title %}{% endblock %}"
        },
    )
    assert retained(result, "django.template-context") == set()
    assert retained(result, "django.template-tag") == {
        "shop.templatetags.catalog.raw"
    }


def test_inheritance_only_executes_present_blocks_with_parent_context(
    tmp_path,
):
    result = analyze(
        tmp_path,
        view_sources(),
        {
            "detail.html": "{% extends 'base.html' %}{{ book.label }}"
            "{% block absent %}{{ book.never_used }}{% endblock %}"
            "{% block content %}{{ book.label }}{% endblock %}",
            "base.html": "{% with book=unknown %}{% block content %}"
            "{{ object.title }}{% endblock %}{% endwith %}",
        },
    )
    assert retained(result, "django.template-context") == set()


def test_inheritance_overrides_parent_block_members(tmp_path):
    result = analyze(
        tmp_path,
        view_sources(),
        {
            "detail.html": "{% extends 'base.html' %}{% block content %}"
            "{{ book.label }}{% endblock %}",
            "base.html": "{% block content %}{{ book.never_used }}"
            "{% endblock %}{{ object.title }}",
        },
    )
    assert retained(result, "django.template-context") == {
        "shop.models.Book.label",
        "shop.models.Book.title",
    }


@pytest.mark.parametrize(
    "body",
    [
        "def nested():\n        return Callback()\n    return None",
        "return None\n    return Callback()",
        "Callback = unknown\n    return Callback()",
    ],
)
def test_node_returns_from_unused_nested_or_dead_scope_are_not_roots(
    tmp_path, body
):
    source = (
        "from django.template import Library, Node\nregister=Library()\n"
        "class Callback(Node):\n"
        "    def render(self, context): return 'hello'\n"
        "@register.tag\ndef raw(parser, token):\n    " + body
    )
    result = analyze(
        tmp_path,
        {"shop.templatetags.catalog": source},
        {"detail.html": "{% load catalog %}{% raw %}"},
    )
    assert retained(result, "django.template-node") == set()


def test_explicit_text_file_is_scanned(tmp_path):
    source = ast.parse(dedent(LIBRARY))
    index = _Index(
        {tmp_path / "shop" / "templatetags" / "catalog.py": source}, tmp_path
    )
    filename = tmp_path / "email.txt"
    filename.write_text("{% load catalog %}{{ value|pretty }}")
    discovery = SimpleNamespace(
        apps=["shop"],
        settings_module="",
        loaded_modules=set(),
        url_modules=set(),
    )
    run(index, discovery, [filename])
    assert retained(index.result, "django.template-filter") == {
        "shop.templatetags.catalog.presentation"
    }
