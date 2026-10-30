from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.demo import _application, build_demo
from core.models import Course, LearningFlow, Project, TicketApplication
from workflow.models import ChoiceSet, TransitionForm, Workflow

BULK_COUNT = 50


class Command(BaseCommand):
    help = 'Создаёт демо-проекты, процессы, формы и заявки. Пользователь admin/admin.'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true', help='Удалить существующие проекты и процессы')
        parser.add_argument('--bulk', type=int, nargs='?', const=BULK_COUNT, metavar='N',
                            help='Добавить N заявок в существующий демо-проект «Код будущего» (по умолчанию {})'
                            .format(BULK_COUNT))

    @transaction.atomic
    def handle(self, reset=False, bulk=None, **options):
        if bulk is not None:
            self.bulk(bulk)
            return
        if Project.objects.exists():
            if not reset:
                raise CommandError('Данные уже есть. Запустите с --reset, чтобы пересоздать.')
            TicketApplication.objects.all().delete()
            Workflow.objects.all().delete()
            TransitionForm.objects.all().delete()
            ChoiceSet.objects.all().delete()
            Project.objects.all().delete()

        User = get_user_model()
        if not User.objects.filter(username='admin').exists():
            User.objects.create_superuser('admin', 'admin@example.com', 'admin')

        data = build_demo()
        self.stdout.write(self.style.SUCCESS('Создано: {} проектов, {} заявок. Вход: admin / admin'.format(
            len(data['projects']), len(data['applications']))))

    def bulk(self, count):
        User = get_user_model()
        project = Project.objects.filter(title__startswith='Код будущего').first()
        if project is None or not hasattr(project, 'workflow'):
            raise CommandError('Сначала создайте демо-данные без --bulk.')
        course = Course.objects.filter(project=project).first()
        statuses = list(project.workflow.statuses.order_by('order'))
        flows = list(LearningFlow.objects.filter(course=course))
        names = [('Кирилл', 'Орлов'), ('Лариса', 'Белова'), ('Максим', 'Козлов'), ('Нина', 'Морозова'),
                 ('Олег', 'Лебедев'), ('Полина', 'Новикова'), ('Роман', 'Фёдоров'), ('Светлана', 'Волкова'),
                 ('Тимур', 'Алексеев'), ('Ульяна', 'Павлова')]
        start = User.objects.filter(username__startswith='bulk').count()
        for i in range(count):
            first, last = names[i % len(names)]
            user, _ = User.objects.get_or_create(username='bulk{}'.format(start + i + 1),
                                                 defaults={'first_name': first, 'last_name': '{} {}'.format(last, i + 1)})
            status = statuses[i % len(statuses)]
            _application(project, course, user, status, flow=flows[i % len(flows)] if flows else None)
        self.stdout.write(self.style.SUCCESS('Добавлено заявок: {}. Всего: {}.'.format(
            count, TicketApplication.objects.count())))
