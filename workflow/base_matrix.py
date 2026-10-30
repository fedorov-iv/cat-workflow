"""
Базовые статусы заявки и допустимые переходы между ними.

Это контракт с интеграциями (Kafka, 1С, ЛК слушателя, внешние API): они видят только базовый статус.
Проектные статусы из конструктора всегда сопоставлены с одним из базовых, а переход между
разными базовыми статусами допустим только если он есть в BASE_TRANSITIONS.
"""

REVIEW = 'review'
APPROVED = 'approved'
APPROVED_EMPLOYER = 'approved_employer'
ADMITTED = 'admitted'
ACCEPTED = 'accepted'
NOT_ADMITTED = 'not_admitted'
DECLINED = 'declined'
CHANGED = 'changed'
EXPELLED = 'expelled'
FINISHED = 'finished'
TRANSFER = 'transfer'

BASE_STATUSES = (
    (REVIEW, 'На рассмотрении'),
    (APPROVED, 'Одобрена'),
    (APPROVED_EMPLOYER, 'Одобрена Работодателем'),
    (ADMITTED, 'Допущен'),
    (ACCEPTED, 'Зачислен'),
    (NOT_ADMITTED, 'Не допущен'),
    (DECLINED, 'Отклонена'),
    (CHANGED, 'Перезаписан'),
    (EXPELLED, 'Отчислен'),
    (FINISHED, 'Окончил обучение'),
    (TRANSFER, 'Перевод'),
)

BASE_TRANSITIONS = {
    REVIEW: {APPROVED, ACCEPTED, DECLINED},
    APPROVED: {ACCEPTED, DECLINED, APPROVED_EMPLOYER, NOT_ADMITTED, ADMITTED, TRANSFER},
    APPROVED_EMPLOYER: {ACCEPTED, NOT_ADMITTED, ADMITTED},
    ADMITTED: {ACCEPTED, DECLINED},
    NOT_ADMITTED: {ADMITTED},
    ACCEPTED: {EXPELLED, FINISHED, TRANSFER},
}

# Атрибуты заявки, которые обязаны быть заполнены после перехода в базовый статус.
BASE_REQUIRED_ATTRS = {
    EXPELLED: ('reason_choice',),
}


def is_base_transition_allowed(from_base, to_base):
    return from_base == to_base or to_base in BASE_TRANSITIONS.get(from_base, ())


def base_status_title(code):
    return dict(BASE_STATUSES).get(code, code)
