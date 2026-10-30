from dataclasses import dataclass, field
from typing import Any


@dataclass
class TransitionContext:
    application: Any
    transition: Any
    actor: Any
    channel: str
    from_status: Any = None
    cleaned_data: dict = field(default_factory=dict)
    _dirty: dict = field(default_factory=dict, repr=False)

    @property
    def project(self):
        return self.application.project

    def mark_dirty(self, obj, *field_names):
        _, fields = self._dirty.setdefault(id(obj), (obj, set()))
        fields.update(field_names)

    def flush(self):
        """Сохраняет изменённые объекты: сначала связанные, затем саму заявку."""
        pending = list(self._dirty.values())
        self._dirty.clear()
        for obj, fields in sorted(pending, key=lambda item: item[0] is self.application):
            if fields:
                obj.save(update_fields=sorted(fields))
