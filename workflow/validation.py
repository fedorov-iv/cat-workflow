from collections import defaultdict, deque
from dataclasses import dataclass

from .base_matrix import BASE_REQUIRED_ATTRS, base_status_title, is_base_transition_allowed
from .forms import parent_link
from .models import CHANNEL_API, CHANNEL_SYSTEM, CHANNELS
from .registry import bindings, check_params, effects, guards

ERROR = 'error'
WARNING = 'warning'


@dataclass
class Issue:
    level: str
    message: str

    def __str__(self):
        return '{}: {}'.format('Ошибка' if self.level == ERROR else 'Предупреждение', self.message)


def validate_workflow(workflow):
    issues = []
    statuses = list(workflow.statuses.all())
    transitions = list(workflow.transitions.select_related('from_status', 'to_status', 'form')
                       .prefetch_related('guards', 'effects', 'form__field_configs'))
    status_ids = {s.id for s in statuses}
    known_channels = {code for code, _ in CHANNELS}

    if not statuses:
        return [Issue(ERROR, 'Не задано ни одного статуса')]
    if workflow.initial_status_id is None:
        issues.append(Issue(ERROR, 'Не задан начальный статус'))
    elif workflow.initial_status_id not in status_ids:
        issues.append(Issue(ERROR, 'Начальный статус принадлежит другому процессу'))

    outgoing = defaultdict(list)
    for t in transitions:
        name = '«{}» ({} → {})'.format(t.button_label, t.from_status.code, t.to_status.code)
        if t.from_status_id not in status_ids or t.to_status_id not in status_ids:
            issues.append(Issue(ERROR, 'Переход {} ссылается на статус другого процесса'.format(name)))
            continue
        if not t.is_active:
            continue
        outgoing[t.from_status_id].append(t)

        if not is_base_transition_allowed(t.from_status.base_status, t.to_status.base_status):
            issues.append(Issue(ERROR, 'Переход {}: базовая матрица запрещает {} → {}'.format(
                name, base_status_title(t.from_status.base_status), base_status_title(t.to_status.base_status))))

        unknown_channels = set(t.channels or []) - known_channels
        if unknown_channels:
            issues.append(Issue(ERROR, 'Переход {}: неизвестные каналы {}'.format(name, sorted(unknown_channels))))
        if not t.channels:
            issues.append(Issue(WARNING, 'Переход {}: не выбран ни один канал'.format(name)))

        for row in t.guards.all():
            if row.code not in guards:
                issues.append(Issue(ERROR, 'Переход {}: неизвестное условие {}'.format(name, row.code)))
                continue
            for problem in check_params(guards.get(row.code).func, row.params):
                issues.append(Issue(ERROR, 'Переход {}, условие {}: {}'.format(name, row.code, problem)))
        for row in t.effects.all():
            if row.code not in effects:
                issues.append(Issue(ERROR, 'Переход {}: неизвестное действие {}'.format(name, row.code)))
                continue
            for problem in check_params(effects.get(row.code).func, row.params):
                issues.append(Issue(ERROR, 'Переход {}, действие {}: {}'.format(name, row.code, problem)))

        issues.extend(_validate_form(t, name))

    for status_id, items in outgoing.items():
        by_code = defaultdict(int)
        for t in items:
            by_code[t.code] += 1
        for code, count in by_code.items():
            if count > 1:
                issues.append(Issue(ERROR, 'Из одного статуса несколько переходов с кодом {}'.format(code)))
        for channel in (CHANNEL_API, CHANNEL_SYSTEM):
            by_base = defaultdict(list)
            for t in items:
                if channel in t.channels:
                    by_base[t.to_status.base_status].append(t.code)
            for base, codes in by_base.items():
                if len(codes) > 1:
                    issues.append(Issue(WARNING, 'Канал {}: из одного статуса несколько переходов в базовый «{}» '
                                                 '({}), переход по базовому статусу будет неоднозначным'.format(
                                                     channel, base_status_title(base), ', '.join(codes))))

    by_id = {s.id: s for s in statuses}
    for status in statuses:
        if status.is_final and outgoing.get(status.id):
            issues.append(Issue(WARNING, 'Конечный статус «{}» имеет исходящие переходы'.format(status.title)))
        if not status.is_final and not outgoing.get(status.id):
            issues.append(Issue(WARNING, 'Из статуса «{}» нет переходов, но он не отмечен конечным'.format(
                status.title)))

    if workflow.initial_status_id in status_ids:
        reachable = {workflow.initial_status_id}
        queue = deque([workflow.initial_status_id])
        while queue:
            for t in outgoing.get(queue.popleft(), []):
                if t.to_status_id not in reachable:
                    reachable.add(t.to_status_id)
                    queue.append(t.to_status_id)
        for status_id in status_ids - reachable:
            issues.append(Issue(WARNING, 'Статус «{}» недостижим из начального'.format(by_id[status_id].title)))

    unique = {(issue.level, issue.message): issue for issue in issues}
    return list(unique.values())


def _validate_form(transition, name):
    issues = []
    required_attrs = set(BASE_REQUIRED_ATTRS.get(transition.to_status.base_status, ()))
    if transition.from_status.base_status == transition.to_status.base_status:
        required_attrs = set()
    if transition.form is None:
        for attr in sorted(required_attrs):
            issues.append(Issue(ERROR, 'Переход {}: для базового статуса «{}» форма должна заполнять {}'.format(
                name, base_status_title(transition.to_status.base_status), attr)))
        return issues

    issues.extend(validate_form(transition.form))
    provided = set()
    for config in transition.form.field_configs.all():
        if config.binding in bindings and config.required and not config.visible_if_param:
            provided |= bindings.get(config.binding).provides
    for attr in sorted(required_attrs - provided):
        issues.append(Issue(ERROR, 'Переход {}: для базового статуса «{}» форма должна обязательно заполнять {}'.format(
            name, base_status_title(transition.to_status.base_status), attr)))
    return issues


def validate_form(form_def):
    """Проверки самой формы, не зависящие от перехода: привязки, условия видимости, списки."""
    issues = []
    prefix = 'Форма {}'.format(form_def.code)
    configs = {c.id: c for c in form_def.field_configs.select_related('choice_set', 'depends_on')}
    known = {cid: c for cid, c in configs.items() if c.binding in bindings}
    for config in configs.values():
        if config.id not in known:
            issues.append(Issue(ERROR, '{}: неизвестная привязка {}'.format(prefix, config.binding)))
    param_names = {bindings.get(c.binding).name(c) for c in known.values()}

    for config in known.values():
        binding = bindings.get(config.binding)
        where = '{}, поле «{}»'.format(prefix, config)
        if config.visible_if_param and config.visible_if_param not in param_names:
            issues.append(Issue(WARNING, '{}: условие видимости ссылается на отсутствующий параметр {}'.format(
                prefix, config.visible_if_param)))
        is_list = binding.is_list(config)
        if config.widget == config.WIDGET_AUTOCOMPLETE and not is_list:
            issues.append(Issue(WARNING, '{}: автокомплит задан у поля, которое не является списком'.format(where)))
        if config.depends_on_id is None:
            continue
        parent = known.get(config.depends_on_id)
        if config.depends_on_id not in configs:
            issues.append(Issue(ERROR, '{}: зависит от поля другой формы'.format(where)))
        elif config.depends_on_id == config.id:
            issues.append(Issue(ERROR, '{}: зависит само от себя'.format(where)))
        elif not is_list:
            issues.append(Issue(ERROR, '{}: зависимость задана у поля, которое не является списком'.format(where)))
        elif parent is not None and not bindings.get(parent.binding).is_list(parent):
            issues.append(Issue(ERROR, '{}: родитель «{}» не является списком'.format(where, parent)))
        elif parent is not None:
            issues.extend(_validate_dependency(where, config, binding, parent))

    for config in known.values():
        seen, current = set(), config
        while current is not None and current.depends_on_id is not None:
            if current.id in seen:
                issues.append(Issue(ERROR, '{}: зависимости списков образуют цикл через «{}»'.format(prefix, config)))
                break
            seen.add(current.id)
            current = known.get(current.depends_on_id)
    return issues


def _validate_dependency(where, config, binding, parent):
    if config.choice_set_id:
        if not any(item.parent_value_list() for item in config.choice_set.items.all()):
            return [Issue(WARNING, '{}: в справочнике «{}» не заполнено «При значениях родителя» — список не будет '
                                   'сужаться'.format(where, config.choice_set))]
        return []
    child_model = binding.option_model(config)
    parent_model = bindings.get(parent.binding).option_model(parent)
    if child_model is None or parent_model is None:
        return [Issue(ERROR, '{}: список из модели может зависеть только от списка из модели'.format(where))]
    if parent_link(child_model, parent_model) is None:
        return [Issue(ERROR, '{}: у модели «{}» нет однозначной связи с «{}»'.format(
            where, child_model._meta.verbose_name, parent_model._meta.verbose_name))]
    return []


def has_errors(issues):
    return any(issue.level == ERROR for issue in issues)
