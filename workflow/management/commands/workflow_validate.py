from django.core.management.base import BaseCommand, CommandError

from workflow.models import Workflow
from workflow.validation import ERROR, has_errors, validate_workflow


class Command(BaseCommand):
    help = 'Проверка конфигурации бизнес-процессов (код выхода 1, если есть ошибки)'

    def add_arguments(self, parser):
        parser.add_argument('project_id', type=int, nargs='?')

    def handle(self, project_id=None, **options):
        workflows = Workflow.objects.select_related('project')
        if project_id:
            workflows = workflows.filter(project_id=project_id)
        failed = False
        for workflow in workflows:
            issues = validate_workflow(workflow)
            self.stdout.write('{}: {}'.format(workflow, 'OK' if not issues else ''))
            for issue in issues:
                style = self.style.ERROR if issue.level == ERROR else self.style.WARNING
                self.stdout.write('  ' + style(str(issue)))
            failed = failed or has_errors(issues)
        if failed:
            raise CommandError('Найдены ошибки конфигурации')
