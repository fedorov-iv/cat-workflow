from django.apps import AppConfig
from django.db.backends.signals import connection_created


class CoreConfig(AppConfig):
    name = 'core'
    verbose_name = 'Заявки (упрощённая копия rall)'

    def ready(self):
        from . import receivers, workflow_hooks  # noqa: F401
        from .sqlite import register_unicode_like
        connection_created.connect(register_unicode_like, dispatch_uid='core.register_unicode_like')
