import json
from dataclasses import dataclass

from django import forms
from django.db import models

from .models import TransitionFormField
from .registry import bindings

TRUE_VALUES = ('on', 'true', '1', 'yes')


@dataclass(eq=False)
class FieldSpec:
    config: object
    binding: object
    fields: dict
    parent: 'FieldSpec' = None
    # Значение варианта -> значения родителя, при которых он доступен. Вариантов без записи это не касается.
    option_parents: dict = None

    @property
    def names(self):
        return list(self.fields)

    @property
    def name(self):
        return self.binding.name(self.config)

    @property
    def field(self):
        return self.fields.get(self.name)


def condition_met(config, data):
    if not config.visible_if_param:
        return True
    raw = data.get(config.visible_if_param)
    expected = config.visible_if_value.strip()
    if expected.lower() in ('true', 'false'):
        actual = raw is True or str(raw or '').strip().lower() in TRUE_VALUES
        return actual == (expected.lower() == 'true')
    return str(raw or '') == expected


def parent_link(child_model, parent_model):
    """Внешний ключ модели вариантов на модель родительского списка; None, если его нет или их несколько."""
    links = [f for f in child_model._meta.get_fields()
             if f.many_to_one and f.concrete and f.related_model is parent_model]
    return links[0] if len(links) == 1 else None


def _option_key(value):
    return '' if value in (None, '') else str(value.pk if isinstance(value, models.Model) else value)


class DependentSelect(forms.Select):
    def __init__(self, attrs=None, choices=(), option_parents=None):
        super().__init__(attrs, choices)
        self.option_parents = option_parents or {}

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        parents = self.option_parents.get(str(value))
        if parents is not None:
            option['attrs']['data-parent'] = json.dumps(sorted(parents))
        return option


class TransitionDynamicForm(forms.Form):
    """Форма, собранная из TransitionFormField. Поля с невыполненным условием видимости игнорируются."""

    def __init__(self, *args, specs, **kwargs):
        super().__init__(*args, **kwargs)
        self.specs = specs
        self._inactive = set()
        for spec in specs:
            for name, field in spec.fields.items():
                self.fields[name] = field
        if self.is_bound:
            for spec in specs:
                if not condition_met(spec.config, self.data):
                    self._inactive.add(id(spec))
                    for name in spec.names:
                        self.fields[name].required = False

    def clean(self):
        cleaned = super().clean()
        for spec in self.specs:
            if id(spec) in self._inactive:
                for name in spec.names:
                    cleaned.pop(name, None)
        for spec in self.active_specs():
            if spec.parent is None:
                continue
            allowed = spec.option_parents.get(_option_key(cleaned.get(spec.name)))
            if allowed is None:
                continue
            parent_value = '' if id(spec.parent) in self._inactive else _option_key(cleaned.get(spec.parent.name))
            if parent_value not in allowed:
                self.add_error(spec.name, 'Значение не подходит к выбранному в поле «{}»'.format(
                    spec.parent.field.label))
        return cleaned

    def active_specs(self):
        return [spec for spec in self.specs if id(spec) not in self._inactive]

    def visibility_rules(self):
        rules = {}
        for spec in self.specs:
            if spec.config.visible_if_param:
                for name in spec.names:
                    rules[name] = (spec.config.visible_if_param, spec.config.visible_if_value)
        return rules


def _option_parents(spec, parent):
    field, parent_field = spec.field, parent.field
    if not isinstance(field, forms.ChoiceField) or not isinstance(parent_field, forms.ChoiceField):
        return None
    if spec.config.choice_set_id:
        return {item.value: set(item.parent_value_list())
                for item in spec.config.choice_set.items.filter(is_active=True) if item.parent_value_list()}
    if isinstance(field, forms.ModelChoiceField) and isinstance(parent_field, forms.ModelChoiceField):
        link = parent_link(field.queryset.model, parent_field.queryset.model)
        if link is not None:
            return {str(pk): {str(parent_pk)}
                    for pk, parent_pk in field.queryset.values_list('pk', link.attname) if parent_pk is not None}
    return None


def _setup_list(spec, parent):
    field = spec.field
    if not isinstance(field, forms.ChoiceField):
        return
    option_parents = _option_parents(spec, parent) if parent is not None else None
    if option_parents is not None:
        spec.parent, spec.option_parents = parent, option_parents
        widget = DependentSelect(attrs=field.widget.attrs, option_parents=option_parents)
        widget.choices = field.choices
        widget.is_required = field.required
        widget.attrs['data-depends-on'] = parent.name
        field.widget = widget
    if spec.config.widget == TransitionFormField.WIDGET_AUTOCOMPLETE:
        field.widget.attrs['data-autocomplete'] = 'local'


def build_form(transition, application, data=None, files=None):
    form_def = transition.form
    if form_def is None:
        return None
    specs = []
    initial = {}
    for config in form_def.field_configs.select_related('choice_set').order_by('order', 'id'):
        binding = bindings.get(config.binding)
        specs.append(FieldSpec(config=config, binding=binding, fields=binding.build_fields(application, config)))
        initial.update(binding.initial(application, config))
    by_id = {spec.config.id: spec for spec in specs}
    for spec in specs:
        _setup_list(spec, by_id.get(spec.config.depends_on_id))
    return TransitionDynamicForm(data=data, files=files, initial=initial, specs=specs)


def form_schema(form):
    """Описание формы в JSON для внешних клиентов (API, будущий SPA-фронтенд)."""
    if form is None:
        return None
    rules = form.visibility_rules()
    lists = {spec.name: spec for spec in form.specs if isinstance(spec.field, forms.ChoiceField)}
    schema = []
    for name, field in form.fields.items():
        item = {
            'name': name,
            'label': str(field.label or name),
            'type': field.__class__.__name__,
            'required': field.required,
            'help_text': str(field.help_text or ''),
        }
        spec = lists.get(name)
        if spec is not None:
            item['widget'] = spec.config.widget
            option_parents = spec.option_parents or {}
            item['choices'] = []
            for value, label in field.choices:
                if value == '':
                    continue
                choice = {'value': str(value), 'label': str(label)}
                if str(value) in option_parents:
                    choice['parents'] = sorted(option_parents[str(value)])
                item['choices'].append(choice)
            if spec.parent is not None:
                item['depends_on'] = spec.parent.name
        if name in form.initial:
            item['initial'] = str(form.initial[name])
        if name in rules:
            item['visible_if'] = {'param': rules[name][0], 'value': rules[name][1]}
        schema.append(item)
    return schema
