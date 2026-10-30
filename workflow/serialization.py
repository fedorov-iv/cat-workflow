"""Экспорт/импорт конфигурации процесса в JSON: перенос между стендами и клонирование между проектами."""
from django.db import transaction

from .models import (ChoiceItem, ChoiceSet, TransitionEffect, TransitionForm, TransitionFormField, TransitionGuard,
                     Workflow, WorkflowStatus, WorkflowTransition)

FORMAT_VERSION = 1

FIELD_ATTRS = ('binding', 'param_name', 'label', 'help_text', 'required', 'order', 'widget',
               'visible_if_param', 'visible_if_value')
TRANSITION_ATTRS = ('code', 'button_label', 'button_style', 'confirm_text', 'channels', 'order', 'is_active')


def export_workflow(workflow):
    transitions = workflow.transitions.select_related('from_status', 'to_status', 'form').order_by('order', 'id')
    forms, choice_sets = {}, {}
    exported_transitions = []
    for t in transitions:
        if t.form_id and t.form.code not in forms:
            forms[t.form.code] = _export_form(t.form, choice_sets)
        item = {attr: getattr(t, attr) for attr in TRANSITION_ATTRS}
        item.update({
            'from': t.from_status.code,
            'to': t.to_status.code,
            'form': t.form.code if t.form_id else None,
            'guards': [{'code': g.code, 'params': g.params, 'error_message': g.error_message, 'order': g.order}
                       for g in t.guards.all()],
            'effects': [{'code': e.code, 'params': e.params, 'order': e.order} for e in t.effects.all()],
        })
        exported_transitions.append(item)
    return {
        'version': FORMAT_VERSION,
        'title': workflow.title,
        'initial_status': workflow.initial_status.code if workflow.initial_status_id else None,
        'statuses': [{'code': s.code, 'title': s.title, 'base_status': s.base_status, 'is_final': s.is_final,
                      'order': s.order} for s in workflow.statuses.all()],
        'transitions': exported_transitions,
        'forms': forms,
        'choice_sets': choice_sets,
    }


def _export_form(form, choice_sets):
    fields = []
    configs = list(form.field_configs.select_related('choice_set'))
    position = {config.id: index for index, config in enumerate(configs)}
    for config in configs:
        item = {attr: getattr(config, attr) for attr in FIELD_ATTRS}
        item['choice_set'] = config.choice_set.code if config.choice_set_id else None
        # Номер родительского поля в списке fields этой формы.
        item['depends_on'] = position.get(config.depends_on_id)
        if config.choice_set_id and config.choice_set.code not in choice_sets:
            choice_sets[config.choice_set.code] = {
                'title': config.choice_set.title,
                'items': [{'value': i.value, 'label': i.label, 'order': i.order, 'is_active': i.is_active,
                           'parent_values': i.parent_values}
                          for i in config.choice_set.items.all()],
            }
        fields.append(item)
    return {'title': form.title, 'description': form.description, 'fields': fields}


@transaction.atomic
def import_workflow(data, project, title=None):
    """
    Создаёт неактивный процесс для проекта. Справочники и формы с совпадающим кодом переиспользуются
    (они общие для проектов), отсутствующие создаются.
    """
    if data.get('version') != FORMAT_VERSION:
        raise ValueError('Неподдерживаемая версия формата: {}'.format(data.get('version')))
    if Workflow.objects.filter(project=project).exists():
        raise ValueError('У проекта {} уже есть бизнес-процесс'.format(project))

    for code, cs_data in data.get('choice_sets', {}).items():
        choice_set, created = ChoiceSet.objects.get_or_create(code=code, defaults={'title': cs_data['title']})
        if created:
            for item in cs_data['items']:
                ChoiceItem.objects.create(choice_set=choice_set, **item)

    forms = {}
    for code, form_data in data.get('forms', {}).items():
        form, created = TransitionForm.objects.get_or_create(
            code=code, defaults={'title': form_data['title'], 'description': form_data.get('description', '')})
        if created:
            created_fields, parents = [], []
            for field_data in form_data['fields']:
                field_data = dict(field_data)
                cs_code = field_data.pop('choice_set')
                parents.append(field_data.pop('depends_on', None))
                created_fields.append(TransitionFormField.objects.create(
                    form=form, choice_set=ChoiceSet.objects.get(code=cs_code) if cs_code else None, **field_data))
            for config, parent_index in zip(created_fields, parents):
                if parent_index is not None:
                    config.depends_on = created_fields[parent_index]
                    config.save(update_fields=['depends_on'])
        forms[code] = form

    workflow = Workflow.objects.create(project=project, title=title or data['title'], is_active=False)
    statuses = {s['code']: WorkflowStatus.objects.create(workflow=workflow, **s) for s in data['statuses']}
    if data.get('initial_status'):
        workflow.initial_status = statuses[data['initial_status']]
        workflow.save(update_fields=['initial_status'])

    for t_data in data['transitions']:
        transition = WorkflowTransition.objects.create(
            workflow=workflow,
            from_status=statuses[t_data['from']],
            to_status=statuses[t_data['to']],
            form=forms[t_data['form']] if t_data.get('form') else None,
            **{attr: t_data[attr] for attr in TRANSITION_ATTRS},
        )
        for g in t_data.get('guards', []):
            TransitionGuard.objects.create(transition=transition, **g)
        for e in t_data.get('effects', []):
            TransitionEffect.objects.create(transition=transition, **e)
    return workflow


def clone_workflow(workflow, project, title=None):
    return import_workflow(export_workflow(workflow), project, title=title)
