import logging
from dataclasses import dataclass, field
from uuid import uuid4

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone

from .base_matrix import BASE_REQUIRED_ATTRS, base_status_title, is_base_transition_allowed
from .context import TransitionContext
from .forms import build_form
from .models import CHANNEL_ARM, TransitionLog
from .registry import effects, guards
from .signals import transition_executed

logger = logging.getLogger(__name__)


class TransitionError(Exception):
    def __init__(self, code, message, form=None, reasons=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.form = form
        self.reasons = reasons or []


@dataclass
class TransitionOption:
    transition: object
    allowed: bool
    reasons: list = field(default_factory=list)


@dataclass
class TransitionResult:
    application: object
    log: object


def get_workflow(application):
    try:
        workflow = application.project.workflow
    except ObjectDoesNotExist:
        return None
    return workflow if workflow.is_active else None


def resolve_current_status(application, workflow):
    status = application.workflow_status
    if status is not None and status.workflow_id == workflow.id:
        return status
    # Заявка создана до включения конструктора: берём первый статус проекта с тем же базовым статусом.
    return workflow.statuses.filter(base_status=application.status).order_by('order', 'id').first()


def assign_initial_status(application):
    """Проставляет начальный статус новой заявке. Возвращает True, если у проекта есть активный workflow."""
    workflow = get_workflow(application)
    if workflow is None or workflow.initial_status is None:
        return False
    application.workflow_status = workflow.initial_status
    application.status = workflow.initial_status.base_status
    return True


def display_status(application):
    status = application.workflow_status
    if status is not None:
        return status.title
    return base_status_title(application.status)


def _evaluate_guards(transition, ctx):
    reasons = []
    for row in transition.guards.all():
        guard = guards.get(row.code)
        try:
            ok = guard.func(ctx, **(row.params or {}))
        except TypeError as e:
            raise TransitionError('config_error', 'Некорректные параметры условия {}: {}'.format(row.code, e))
        if not ok:
            reasons.append(row.error_message or guard.message)
    return reasons


def _outgoing(workflow, current):
    return (workflow.transitions
            .filter(is_active=True, from_status=current)
            .select_related('to_status', 'from_status', 'form')
            .prefetch_related('guards', 'effects')
            .order_by('order', 'id'))


def available_transitions(application, actor=None, channel=CHANNEL_ARM, include_blocked=False):
    workflow = get_workflow(application)
    if workflow is None:
        return []
    current = resolve_current_status(application, workflow)
    if current is None:
        return []
    options = []
    for transition in _outgoing(workflow, current):
        if channel not in transition.channels:
            continue
        ctx = TransitionContext(application, transition, actor, channel, from_status=current)
        reasons = _evaluate_guards(transition, ctx)
        if reasons and not include_blocked:
            continue
        options.append(TransitionOption(transition, allowed=not reasons, reasons=reasons))
    return options


def get_form(transition, application, data=None, files=None):
    return build_form(transition, application, data=data, files=files)


def execute(application, transition, actor, channel=CHANNEL_ARM, data=None, files=None):
    """
    Выполняет переход: проверка статуса и канала, условия, валидация формы, запись привязанных полей,
    смена статуса, действия, журнал. Всё в одной транзакции; уведомления — после коммита.
    """
    with transaction.atomic():
        app = type(application).objects.select_for_update().get(pk=application.pk)
        workflow = get_workflow(app)
        if workflow is None or transition.workflow_id != workflow.id:
            raise TransitionError('no_workflow', 'Для проекта заявки не настроен этот бизнес-процесс')
        if not transition.is_active:
            raise TransitionError('inactive', 'Переход «{}» отключён'.format(transition.button_label))
        if channel not in transition.channels:
            raise TransitionError('channel_forbidden',
                                  'Переход «{}» недоступен из канала {}'.format(transition.button_label, channel))

        current = resolve_current_status(app, workflow)
        if current is None or transition.from_status_id != current.id:
            raise TransitionError('wrong_status', 'Переход «{}» недоступен из статуса «{}»'.format(
                transition.button_label, current.title if current else base_status_title(app.status)))

        to_status = transition.to_status
        if not is_base_transition_allowed(current.base_status, to_status.base_status):
            raise TransitionError('contract_violation', 'Базовая матрица запрещает переход {} → {}'.format(
                current.base_status, to_status.base_status))

        ctx = TransitionContext(app, transition, actor, channel, from_status=current)
        reasons = _evaluate_guards(transition, ctx)
        if reasons:
            raise TransitionError('guard_failed', '; '.join(reasons), reasons=reasons)

        written = []
        form = build_form(transition, app, data=data or {}, files=files or {})
        if form is not None:
            if not form.is_valid():
                raise TransitionError('invalid_form', 'Проверьте данные формы', form=form)
            ctx.cleaned_data = form.cleaned_data
            for spec in form.active_specs():
                spec.binding.save(ctx, {name: form.cleaned_data.get(name) for name in spec.names}, spec.config)
                written.append(spec.binding.key)

        for attr in BASE_REQUIRED_ATTRS.get(to_status.base_status, ()):
            if not getattr(app, attr, None):
                raise TransitionError('contract_violation',
                                      'Для статуса «{}» обязательно поле {}'.format(to_status.title, attr))

        from_base = app.status
        app.workflow_status = to_status
        app.status = to_status.base_status
        app._status = '{}_{}'.format(to_status.base_status, uuid4())
        app.reviewed_by = actor
        app.reviewed_at = timezone.now()
        ctx.mark_dirty(app, 'workflow_status', 'status', '_status', 'reviewed_by', 'reviewed_at')
        ctx.flush()

        for row in transition.effects.all():
            effect = effects.get(row.code)
            try:
                effect.func(ctx, **(row.params or {}))
            except TypeError as e:
                raise TransitionError('config_error', 'Некорректные параметры действия {}: {}'.format(row.code, e))
            ctx.flush()

        log = TransitionLog.objects.create(
            application=app, transition=transition, from_status=current, to_status=to_status,
            from_base_status=from_base, to_base_status=to_status.base_status,
            actor=actor, channel=channel, written_bindings=written,
        )
        logger.info('application %s: %s -> %s via %s (%s)', app.pk, current.code, to_status.code,
                    transition.code, channel)
        transaction.on_commit(lambda: transition_executed.send(
            sender=type(app), application=app, transition=transition, log=log))

    return TransitionResult(application=app, log=log)


def _find(application, channel, predicate, not_found_message):
    workflow = get_workflow(application)
    if workflow is None:
        raise TransitionError('no_workflow', 'Для проекта заявки не настроен бизнес-процесс')
    current = resolve_current_status(application, workflow)
    if current is None:
        raise TransitionError('wrong_status', 'Статус заявки не найден в бизнес-процессе проекта')
    candidates = [t for t in _outgoing(workflow, current) if channel in t.channels and predicate(t)]
    if not candidates:
        raise TransitionError('no_transition', not_found_message.format(current=current.title))
    if len(candidates) > 1:
        raise TransitionError('ambiguous', 'Найдено несколько подходящих переходов: {}'.format(
            ', '.join(t.code for t in candidates)))
    return candidates[0]


def execute_by_code(application, code, actor, channel=CHANNEL_ARM, data=None, files=None):
    transition = _find(application, channel, lambda t: t.code == code,
                       'Из статуса «{current}» нет перехода «%s»' % code)
    return execute(application, transition, actor, channel, data, files)


def transition_to_base(application, base_status, actor, channel, data=None, files=None):
    """Адаптер для вызовов, которые оперируют базовым статусом (API платформ, задачи, отписка в ЛК)."""
    transition = _find(application, channel, lambda t: t.to_status.base_status == base_status,
                       'Из статуса «{current}» нет перехода в «%s»' % base_status_title(base_status))
    return execute(application, transition, actor, channel, data, files)
