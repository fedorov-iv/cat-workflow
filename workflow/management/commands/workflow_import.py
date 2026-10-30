import json

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from workflow.serialization import import_workflow


class Command(BaseCommand):
    help = 'Импорт бизнес-процесса из JSON в проект (создаётся неактивным)'

    def add_arguments(self, parser):
        parser.add_argument('path')
        parser.add_argument('project_id', type=int)

    def handle(self, path, project_id, **options):
        project = apps.get_model(settings.WORKFLOW_PROJECT_MODEL).objects.filter(pk=project_id).first()
        if project is None:
            raise CommandError('Проект {} не найден'.format(project_id))
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        try:
            workflow = import_workflow(data, project)
        except ValueError as e:
            raise CommandError(str(e))
        self.stdout.write(self.style.SUCCESS('Создан процесс #{} «{}»'.format(workflow.pk, workflow.title)))
