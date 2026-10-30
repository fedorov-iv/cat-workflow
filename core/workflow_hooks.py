"""
Блоки конструктора, которые предоставляет хост: привязки к полям заявки и трансферта,
проектные условия и побочные эффекты. В cat-marketplace это будет rall/workflow_hooks.py.
"""
from django import forms
from django.utils import timezone

from workflow.bindings import Binding, ModelFieldBinding
from workflow.registry import effect, guard, register_binding

from .models import CourseUserStatus, LearningFlow, OrgDocument, TicketApplication, Transfert


def application_target(application, ctx=None):
    return application


def transfert_target(application, ctx=None):
    if application.transfert_id is None and ctx is not None:
        application.transfert = Transfert.objects.create()
        ctx.mark_dirty(application, 'transfert')
    return application.transfert


APPLICATION_FIELDS = (
    ('reason_choice', 'Причина'),
    ('reason_detail', 'Уточнение причины'),
    ('reason_text', 'Комментарий к причине'),
    ('bas_declined_date', 'Дата решения об отклонении'),
    ('difficulty_level', 'Уровень'),
    ('final_project_name', 'Название итогового проекта'),
)

TRANSFERT_FIELDS = (
    ('enter_exam_date', 'Дата вступительного испытания'),
    ('accept_date', 'Дата зачисления'),
    ('admission_order_date', 'Дата приказа о зачислении'),
    ('admission_order_number', 'Номер приказа о зачислении'),
    ('expulsion_date', 'Дата отчисления'),
    ('expulsion_order_date', 'Дата приказа об отчислении'),
    ('expulsion_order_number', 'Номер приказа об отчислении'),
    ('employer_pays', 'Оплачивает работодатель'),
    ('employer_name', 'Наименование работодателя'),
    ('employer_ogrn', 'ОГРН работодателя'),
)

for field_name, label in APPLICATION_FIELDS:
    register_binding(ModelFieldBinding(
        key='application.{}'.format(field_name), label='Заявка: {}'.format(label),
        model=TicketApplication, field_name=field_name, target=application_target, provides=(field_name,),
    ))

for field_name, label in TRANSFERT_FIELDS:
    register_binding(ModelFieldBinding(
        key='transfert.{}'.format(field_name), label='Трансферт: {}'.format(label),
        model=Transfert, field_name=field_name, target=transfert_target,
    ))

register_binding(ModelFieldBinding(
    key='application.flow', label='Заявка: Поток', model=TicketApplication, field_name='flow',
    target=application_target, provides=('flow',),
    queryset=lambda application: LearningFlow.objects.filter(course=application.course, is_active=True,
                                                             is_filled=False),
))


class DocumentBinding(Binding):
    """Файл + подпись (ЭП) -> OrgDocument, ссылка пишется в FK-поле заявки."""

    def __init__(self, key, label, param_name, field_name, document_title):
        super().__init__(key, label, param_name, provides=(field_name,))
        self.field_name = field_name
        self.document_title = document_title

    def build_fields(self, application, config):
        name = self.name(config)
        main = self._decorate(forms.FileField(), config, required_default=True, default_label='Документ-основание')
        signature = forms.FileField(label='{} — подпись (ЭП)'.format(main.label), required=False)
        return {name: main, '{}_ds'.format(name): signature}

    def save(self, ctx, values, config):
        name = self.name(config)
        upload = values.get(name)
        if not upload:
            return
        document = OrgDocument.objects.create(
            title='{} — заявка #{}'.format(self.document_title, ctx.application.pk),
            file=upload, signature=values.get('{}_ds'.format(name)) or '', creator=ctx.actor,
        )
        setattr(ctx.application, self.field_name, document)
        ctx.mark_dirty(ctx.application, self.field_name)


register_binding(DocumentBinding(
    key='application.reason_document', label='Заявка: Документ-основание (файл + ЭП)',
    param_name='reason_file', field_name='reason_document', document_title='Основание',
))


@guard('project_expelling_allowed', 'Отчисления разрешены в проекте', 'По условиям проекта отчисление запрещено')
def project_expelling_allowed(ctx):
    return not ctx.application.project.is_expelling_prohibited


@guard('transfert_status_in', 'Статус трансферта входит в список', 'Ожидайте нужный статус трансферта',
       params={'statuses': 'список: booked, activated, conditionally_activated'})
def transfert_status_in(ctx, statuses):
    transfert = ctx.application.transfert
    return transfert is not None and transfert.status in statuses


@guard('flow_not_filled', 'Поток заявки не закрыт', 'Поток заявки закрыт')
def flow_not_filled(ctx):
    return ctx.application.flow is not None and not ctx.application.flow.is_filled


@guard('flow_filled', 'Поток заявки закрыт', 'Поток ещё не закрыт')
def flow_filled(ctx):
    return ctx.application.flow is not None and ctx.application.flow.is_filled


@guard('not_finished_course', 'Слушатель не окончил обучение', 'Нельзя: слушатель уже окончил обучение')
def not_finished_course(ctx):
    app = ctx.application
    if app.dt_finished:
        return False
    return not CourseUserStatus.objects.filter(user=app.user, course=app.course, dt_finish__isnull=False).exists()


@effect('course_user_status_enroll', 'Записать слушателя на курс')
def course_user_status_enroll(ctx):
    app = ctx.application
    CourseUserStatus.objects.update_or_create(user=app.user, course=app.course, defaults={'is_active': True})


@effect('course_user_status_expel', 'Отписать слушателя от курса')
def course_user_status_expel(ctx):
    app = ctx.application
    CourseUserStatus.objects.filter(user=app.user, course=app.course).update(is_active=False)


@effect('course_user_status_finish', 'Отметить окончание обучения')
def course_user_status_finish(ctx):
    app = ctx.application
    now = timezone.now()
    CourseUserStatus.objects.filter(user=app.user, course=app.course).update(dt_finish=now)
    app.dt_finished = now
    ctx.mark_dirty(app, 'dt_finished')


@effect('set_transfert_status', 'Сменить статус трансферта', params={'status': 'booked / activated / ...'})
def set_transfert_status(ctx, status):
    transfert = transfert_target(ctx.application, ctx)
    transfert.status = status
    ctx.mark_dirty(transfert, 'status')
