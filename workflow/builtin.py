"""Универсальные условия, не зависящие от моделей хоста."""
import logging

from django.conf import settings

from .bindings import resolve_path
from .registry import effect, guard

logger = logging.getLogger(__name__)


@guard('setting_flag', 'Флаг в настройках', 'Действие отключено настройками',
       params={'name': 'имя настройки', 'expected': 'ожидаемое значение (по умолчанию true)'})
def setting_flag(ctx, name, expected=True):
    return getattr(settings, name, None) == expected


@guard('attr_in', 'Атрибут заявки входит в список', 'Условие перехода не выполнено',
       params={'path': 'путь, например transfert.status', 'values': 'список допустимых значений'})
def attr_in(ctx, path, values):
    return resolve_path(ctx.application, path) in values


@guard('attr_not_in', 'Атрибут заявки не входит в список', 'Условие перехода не выполнено',
       params={'path': 'путь, например future_code_module', 'values': 'список запрещённых значений'})
def attr_not_in(ctx, path, values):
    return resolve_path(ctx.application, path) not in values


@guard('attr_is_set', 'Атрибут заявки заполнен', 'Не заполнены обязательные данные заявки',
       params={'path': 'путь, например flow'})
def attr_is_set(ctx, path):
    return bool(resolve_path(ctx.application, path))


@effect('log_event', 'Записать событие в лог', params={'message': 'текст'})
def log_event(ctx, message):
    logger.info('application %s: %s', ctx.application.pk, message)
