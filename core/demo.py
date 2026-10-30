"""Демо-данные: три проекта с разными процессами. Используется командой seed_demo и тестами."""
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import transaction

from workflow.models import (CHANNEL_API, CHANNEL_ARM, CHANNEL_SYSTEM, CHANNEL_USER, ChoiceItem, ChoiceSet,
                             TransitionEffect, TransitionForm, TransitionFormField, TransitionGuard, Workflow,
                             WorkflowStatus, WorkflowTransition)

from .models import Course, LearningFlow, Project, TicketApplication, Transfert

ALL_CHANNELS = [CHANNEL_ARM, CHANNEL_API, CHANNEL_SYSTEM]


def _choice_set(code, title, items):
    choice_set = ChoiceSet.objects.create(code=code, title=title)
    for order, (value, label, *parent_values) in enumerate(items):
        ChoiceItem.objects.create(choice_set=choice_set, value=value, label=label, order=order,
                                  parent_values=','.join(parent_values))
    return choice_set


def _form(code, title, fields, description=''):
    """depends_on в описании поля — номер родительского поля в fields."""
    form = TransitionForm.objects.create(code=code, title=title, description=description)
    created = []
    for order, field in enumerate(fields):
        field = dict(field)
        parent = field.pop('depends_on', None)
        created.append(TransitionFormField.objects.create(
            form=form, order=order, depends_on=created[parent] if parent is not None else None, **field))
    return form


def build_forms():
    decline_reasons = _choice_set('decline_reasons', 'Причины отклонения', [
        ('by_recipient', 'Отклонено в связи с обращением заявителя'),
        ('not_support_recipient', 'Заявитель не является получателем поддержки'),
        ('no_places', 'Отсутствие свободного места на площадке'),
        ('other', 'Иное'),
    ])
    expel_reasons = _choice_set('expel_reasons', 'Причины отчисления', [
        ('legitimate', 'Отчислен по уважительной причине'),
        ('not_legitimate', 'Отчислен по неуважительной причине'),
        ('other', 'Другое'),
    ])
    expel_details = _choice_set('expel_details', 'Уточнения причин отчисления', [
        ('illness', 'Болезнь', 'legitimate'),
        ('army', 'Призыв на военную службу', 'legitimate'),
        ('relocation', 'Переезд', 'legitimate', 'not_legitimate'),
        ('absence', 'Не посещал занятия', 'not_legitimate'),
        ('debts', 'Академическая задолженность', 'not_legitimate'),
    ])
    autocomplete = TransitionFormField.WIDGET_AUTOCOMPLETE
    return {
        'approve': _form('approve_flow', 'Одобрение: поток и дата ВИ', [
            {'binding': 'application.flow', 'widget': autocomplete},
            {'binding': 'transfert.enter_exam_date'},
        ]),
        'decline': _form('decline', 'Отклонение заявки', [
            {'binding': 'application.reason_choice', 'label': 'Причина отклонения', 'choice_set': decline_reasons},
            {'binding': 'application.reason_text', 'label': 'Опишите причину',
             'visible_if_param': 'reason_choice', 'visible_if_value': 'other'},
            {'binding': 'application.bas_declined_date'},
        ]),
        'accept': _form('accept', 'Зачисление', [
            {'binding': 'transfert.accept_date'},
            {'binding': 'transfert.admission_order_date'},
            {'binding': 'transfert.admission_order_number'},
            {'binding': 'transfert.employer_pays', 'required': False},
            {'binding': 'transfert.employer_name', 'visible_if_param': 'employer_pays', 'visible_if_value': 'true'},
            {'binding': 'transfert.employer_ogrn', 'visible_if_param': 'employer_pays', 'visible_if_value': 'true'},
        ], description='Данные приказа о зачислении. Если обучение оплачивает работодатель — укажите его.'),
        'expel': _form('expel', 'Отчисление', [
            {'binding': 'application.reason_choice', 'label': 'Причина отчисления', 'choice_set': expel_reasons},
            {'binding': 'application.reason_detail', 'choice_set': expel_details, 'required': False,
             'widget': autocomplete, 'depends_on': 0},
            {'binding': 'application.reason_text', 'label': 'Опишите причину',
             'visible_if_param': 'reason_choice', 'visible_if_value': 'other'},
            {'binding': 'application.reason_document', 'label': 'Подтверждающий документ',
             'visible_if_param': 'reason_choice', 'visible_if_value': 'legitimate'},
            {'binding': 'transfert.expulsion_date'},
            {'binding': 'transfert.expulsion_order_date'},
            {'binding': 'transfert.expulsion_order_number'},
        ]),
        'finish': _form('finish', 'Окончание обучения', [
            {'binding': 'application.final_project_name'},
        ]),
    }


def _statuses(workflow, items):
    result = {}
    for order, (code, title, base, is_final) in enumerate(items):
        result[code] = WorkflowStatus.objects.create(workflow=workflow, code=code, title=title, base_status=base,
                                                     is_final=is_final, order=order)
    return result


def _transition(workflow, statuses, code, src, dst, label, style='primary', form=None, channels=None,
                guards=(), effects=(), order=0, confirm_text=''):
    transition = WorkflowTransition.objects.create(
        workflow=workflow, code=code, from_status=statuses[src], to_status=statuses[dst], button_label=label,
        button_style=style, form=form, channels=channels or [CHANNEL_ARM], order=order, confirm_text=confirm_text,
    )
    for i, (guard_code, params, *message) in enumerate(guards):
        TransitionGuard.objects.create(transition=transition, code=guard_code, params=params, order=i,
                                       error_message=message[0] if message else '')
    for i, (effect_code, params) in enumerate(effects):
        TransitionEffect.objects.create(transition=transition, code=effect_code, params=params, order=i)
    return transition


def build_kb_workflow(project, forms):
    workflow = Workflow.objects.create(project=project, title='Код будущего: процесс с проверкой документов')
    s = _statuses(workflow, [
        ('review', 'На рассмотрении', 'review', False),
        ('approved', 'Одобрена, ждёт ВИ', 'approved', False),
        ('docs_check', 'Проверка документов', 'approved', False),
        ('accepted', 'Зачислен', 'accepted', False),
        ('declined', 'Отклонена', 'declined', True),
        ('expelled', 'Отчислен', 'expelled', True),
        ('finished', 'Окончил обучение', 'finished', True),
    ])
    workflow.initial_status = s['review']
    workflow.save()
    active_transfert = (
        'transfert_status_in', {'statuses': [Transfert.STATUS_ACTIVATED, Transfert.STATUS_CONDITIONALLY_ACTIVATED]},
        'Ожидайте трансферт — активирован / активирован условно',
    )
    _transition(workflow, s, 'approve', 'review', 'approved', 'Одобрить', 'success', forms['approve'],
                channels=ALL_CHANNELS, order=1)
    _transition(workflow, s, 'decline', 'review', 'declined', 'Отклонить', 'danger', forms['decline'],
                channels=ALL_CHANNELS + [CHANNEL_USER], order=9)
    _transition(workflow, s, 'change_flow', 'approved', 'approved', 'Сменить поток', 'secondary', forms['approve'],
                order=2)
    _transition(workflow, s, 'docs_received', 'approved', 'docs_check', 'Документы получены', 'primary',
                guards=[active_transfert], order=1)
    _transition(workflow, s, 'decline', 'approved', 'declined', 'Отклонить', 'danger', forms['decline'],
                channels=ALL_CHANNELS + [CHANNEL_USER], order=9)
    _transition(workflow, s, 'accept', 'docs_check', 'accepted', 'Зачислить', 'success', forms['accept'],
                channels=ALL_CHANNELS, guards=[active_transfert], effects=[('course_user_status_enroll', {})],
                order=1)
    _transition(workflow, s, 'decline', 'docs_check', 'declined', 'Отклонить', 'danger', forms['decline'],
                channels=ALL_CHANNELS, order=9)
    _transition(workflow, s, 'expel', 'accepted', 'expelled', 'Отчислить', 'danger', forms['expel'],
                channels=ALL_CHANNELS,
                guards=[('project_expelling_allowed', {}), ('not_finished_course', {}),
                        ('setting_flag', {'name': 'SHOW_EXPEL_AND_FINISH_BUTTONS'})],
                effects=[('course_user_status_expel', {})], order=9,
                confirm_text='Слушатель будет отписан от курса.')
    _transition(workflow, s, 'finish', 'accepted', 'finished', 'Завершил обучение', 'success', forms['finish'],
                channels=ALL_CHANNELS,
                guards=[('flow_filled', {}, 'Отметить окончание можно после закрытия потока')],
                effects=[('course_user_status_finish', {})], order=1)
    workflow.is_active = True
    workflow.save()
    return workflow, s


def build_bas_workflow(project, forms):
    workflow = Workflow.objects.create(project=project, title='БАС: прямое зачисление')
    s = _statuses(workflow, [
        ('review', 'Новая заявка', 'review', False),
        ('accepted', 'Обучается', 'accepted', False),
        ('declined', 'Отказ', 'declined', True),
        ('expelled', 'Отчислен', 'expelled', True),
        ('finished', 'Выпускник', 'finished', True),
    ])
    workflow.initial_status = s['review']
    workflow.save()
    _transition(workflow, s, 'accept', 'review', 'accepted', 'Зачислить', 'success', forms['accept'],
                channels=ALL_CHANNELS, effects=[('course_user_status_enroll', {})], order=1)
    _transition(workflow, s, 'decline', 'review', 'declined', 'Отказать', 'danger', forms['decline'],
                channels=ALL_CHANNELS + [CHANNEL_USER], order=9)
    _transition(workflow, s, 'expel', 'accepted', 'expelled', 'Отчислить', 'danger', forms['expel'],
                channels=ALL_CHANNELS, guards=[('project_expelling_allowed', {}), ('not_finished_course', {})],
                effects=[('course_user_status_expel', {})], order=9)
    _transition(workflow, s, 'finish', 'accepted', 'finished', 'Выпустить', 'success',
                channels=ALL_CHANNELS, effects=[('course_user_status_finish', {})], order=1)
    workflow.is_active = True
    workflow.save()
    return workflow, s


def _application(project, course, user, status, flow=None, transfert_status=None):
    transfert = Transfert.objects.create(status=transfert_status) if transfert_status else None
    return TicketApplication.objects.create(
        project=project, course=course, user=user, flow=flow, transfert=transfert,
        workflow_status=status, status=status.base_status if status else 'review',
        _status='{}_{}'.format(status.base_status if status else 'review', uuid4()),
    )


@transaction.atomic
def build_demo():
    User = get_user_model()
    names = [('Анна', 'Иванова'), ('Борис', 'Петров'), ('Вера', 'Смирнова'), ('Глеб', 'Кузнецов'),
             ('Дарья', 'Попова'), ('Егор', 'Васильев'), ('Жанна', 'Соколова'), ('Захар', 'Михайлов'),
             ('Ирина', 'Новикова')]
    users = [User.objects.update_or_create(username='student{}'.format(i),
                                           defaults={'first_name': first, 'last_name': last})[0]
             for i, (first, last) in enumerate(names, start=1)]
    forms = build_forms()

    kb = Project.objects.create(title='Код будущего (демо)')
    kb_course = Course.objects.create(title='Python для школьников', project=kb)
    flow1 = LearningFlow.objects.create(course=kb_course, title='Поток 1')
    LearningFlow.objects.create(course=kb_course, title='Поток 2')
    closed_flow = LearningFlow.objects.create(course=kb_course, title='Поток 0', is_filled=True)
    kb_workflow, s = build_kb_workflow(kb, forms)

    bas = Project.objects.create(title='БАС (демо, отчисления запрещены)', is_expelling_prohibited=True)
    bas_course = Course.objects.create(title='Оператор БПЛА', project=bas)
    LearningFlow.objects.create(course=bas_course, title='Весна')
    bas_workflow, b = build_bas_workflow(bas, forms)

    legacy = Project.objects.create(title='Цифровые профессии (без процесса)')
    legacy_course = Course.objects.create(title='Аналитик данных', project=legacy)

    activated = Transfert.STATUS_ACTIVATED
    applications = {
        'kb_review': _application(kb, kb_course, users[0], s['review']),
        'kb_approved_booked': _application(kb, kb_course, users[1], s['approved'], flow1, Transfert.STATUS_BOOKED),
        'kb_approved_active': _application(kb, kb_course, users[2], s['approved'], flow1, activated),
        'kb_docs_check': _application(kb, kb_course, users[3], s['docs_check'], flow1, activated),
        'kb_accepted_closed_flow': _application(kb, kb_course, users[4], s['accepted'], closed_flow, activated),
        'kb_accepted': _application(kb, kb_course, users[5], s['accepted'], flow1, activated),
        'bas_review': _application(bas, bas_course, users[6], b['review']),
        'bas_accepted': _application(bas, bas_course, users[7], b['accepted'], transfert_status=activated),
        'legacy_review': _application(legacy, legacy_course, users[8], None),
    }
    return {
        'projects': {'kb': kb, 'bas': bas, 'legacy': legacy},
        'workflows': {'kb': kb_workflow, 'bas': bas_workflow},
        'statuses': {'kb': s, 'bas': b},
        'forms': forms,
        'applications': applications,
        'flows': {'open': flow1, 'closed': closed_flow},
    }
