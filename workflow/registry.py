"""
Реестры блоков, из которых конструктор собирает процесс.

Поведение живёт только в коде: в админке можно выбрать зарегистрированный блок и задать ему
параметры, но нельзя написать произвольную логику.
"""
import inspect
from dataclasses import dataclass, field
from typing import Callable

from django.core.exceptions import ImproperlyConfigured


class UnknownCode(KeyError):
    pass


@dataclass(frozen=True)
class Guard:
    code: str
    label: str
    func: Callable
    message: str
    params: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Effect:
    code: str
    label: str
    func: Callable
    params: dict = field(default_factory=dict)


class Registry:
    def __init__(self, kind):
        self.kind = kind
        self._items = {}

    def add(self, item):
        if item.code in self._items:
            raise ImproperlyConfigured('{} "{}" уже зарегистрирован'.format(self.kind, item.code))
        self._items[item.code] = item
        return item

    def get(self, code):
        try:
            return self._items[code]
        except KeyError:
            raise UnknownCode('{}: неизвестный код "{}"'.format(self.kind, code))

    def __contains__(self, code):
        return code in self._items

    def all(self):
        return sorted(self._items.values(), key=lambda item: item.code)

    def choices(self):
        return [(item.code, '{} [{}]'.format(item.label, item.code)) for item in self.all()]

    def describe(self):
        lines = []
        for item in self.all():
            params = getattr(item, 'params', None) or {}
            if params:
                desc = ', '.join('{}: {}'.format(k, v) for k, v in params.items())
                lines.append('{} — {}'.format(item.code, desc))
        return lines


bindings = Registry('binding')
guards = Registry('guard')
effects = Registry('effect')


def guard(code, label, message, params=None):
    def decorator(func):
        guards.add(Guard(code=code, label=label, func=func, message=message, params=params or {}))
        return func
    return decorator


def effect(code, label, params=None):
    def decorator(func):
        effects.add(Effect(code=code, label=label, func=func, params=params or {}))
        return func
    return decorator


def register_binding(binding):
    return bindings.add(binding)


def check_params(func, params):
    """Возвращает список проблем с параметрами вызова guard/effect (неизвестные и недостающие)."""
    problems = []
    signature = inspect.signature(func)
    accepted = [p for name, p in signature.parameters.items() if name != 'ctx']
    names = {p.name for p in accepted}
    has_kwargs = any(p.kind == p.VAR_KEYWORD for p in accepted)
    for key in params or {}:
        if key not in names and not has_kwargs:
            problems.append('неизвестный параметр "{}"'.format(key))
    for p in accepted:
        if p.kind in (p.VAR_KEYWORD, p.VAR_POSITIONAL):
            continue
        if p.default is p.empty and p.name not in (params or {}):
            problems.append('не задан обязательный параметр "{}"'.format(p.name))
    return problems
