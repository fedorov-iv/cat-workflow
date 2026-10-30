"""
Привязки полей формы к полям моделей.

Привязка знает, какие поля Django-формы построить, какое у них начальное значение и как сохранить
результат. Данные формы пишутся только в существующие поля моделей хоста.
"""
from django import forms


class Binding:
    def __init__(self, key, label, param_name, provides=()):
        self.key = key
        self.label = label
        self.param_name = param_name
        # Атрибуты заявки, которые гарантированно заполняет привязка (для проверки контракта базовых статусов).
        self.provides = frozenset(provides)

    @property
    def code(self):
        return self.key

    def name(self, config=None):
        return (config.param_name if config is not None and config.param_name else self.param_name)

    def build_fields(self, application, config):
        raise NotImplementedError

    def initial(self, application, config):
        return {}

    def is_list(self, config):
        return False

    def option_model(self, config):
        """Модель вариантов списка; None — варианты из справочника или поле не список."""
        return None

    def save(self, ctx, values, config):
        raise NotImplementedError

    def _decorate(self, field, config, required_default, default_label=None):
        field.label = (config.label if config is not None and config.label else default_label or self.label)
        field.help_text = config.help_text if config is not None else ''
        field.required = config.required if config is not None else required_default
        return field


def resolve_path(obj, path):
    for part in path.split('.'):
        if obj is None:
            return None
        obj = getattr(obj, part, None)
    return obj


class ModelFieldBinding(Binding):
    """
    Привязка к одному полю модели.

    target(application, ctx=None) возвращает объект, в который пишем. Если ctx передан,
    target может создать недостающий объект и пометить изменения через ctx.mark_dirty.
    """

    def __init__(self, key, label, model, field_name, target, param_name=None, provides=(), queryset=None):
        super().__init__(key, label, param_name or field_name, provides)
        self.model = model
        self.field_name = field_name
        self.target = target
        self.queryset = queryset

    def build_fields(self, application, config):
        model_field = self.model._meta.get_field(self.field_name)
        if config is not None and config.choice_set_id:
            field = forms.ChoiceField(choices=[('', '---------')] + config.choice_set.as_choices())
        elif self.queryset is not None:
            field = forms.ModelChoiceField(queryset=self.queryset(application))
        else:
            field = model_field.formfield()
        if isinstance(field, forms.DateField):
            field.widget = forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')
        return {self.name(config): self._decorate(field, config, required_default=not model_field.blank,
                                                  default_label=str(model_field.verbose_name))}

    def is_list(self, config):
        model_field = self.model._meta.get_field(self.field_name)
        return bool(config.choice_set_id or model_field.choices or model_field.many_to_one)

    def option_model(self, config):
        model_field = self.model._meta.get_field(self.field_name)
        return model_field.related_model if model_field.many_to_one and not config.choice_set_id else None

    def initial(self, application, config):
        obj = self.target(application)
        if obj is None:
            return {}
        value = self.model._meta.get_field(self.field_name).value_from_object(obj)
        if value in (None, ''):
            return {}
        return {self.name(config): value}

    def save(self, ctx, values, config):
        obj = self.target(ctx.application, ctx)
        setattr(obj, self.field_name, values[self.name(config)])
        ctx.mark_dirty(obj, self.field_name)
