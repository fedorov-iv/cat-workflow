from django.apps import AppConfig


class WorkflowConfig(AppConfig):
    name = 'workflow'
    verbose_name = 'Конструктор бизнес-процессов'

    def ready(self):
        from . import builtin  # noqa: F401
