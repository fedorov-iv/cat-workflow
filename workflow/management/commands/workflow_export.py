import json

from django.core.management.base import BaseCommand, CommandError

from workflow.models import Workflow
from workflow.serialization import export_workflow


class Command(BaseCommand):
    help = 'Экспорт бизнес-процесса проекта в JSON'

    def add_arguments(self, parser):
        parser.add_argument('project_id', type=int)
        parser.add_argument('--output', '-o', help='Файл; по умолчанию stdout')

    def handle(self, project_id, output=None, **options):
        try:
            workflow = Workflow.objects.get(project_id=project_id)
        except Workflow.DoesNotExist:
            raise CommandError('У проекта {} нет бизнес-процесса'.format(project_id))
        payload = json.dumps(export_workflow(workflow), ensure_ascii=False, indent=2)
        if output:
            with open(output, 'w', encoding='utf-8') as f:
                f.write(payload)
        else:
            self.stdout.write(payload)
