"""OpenAPI-описание API заявок. Эндпоинты — обычные Django-view, схема поддерживается вручную."""
from django.conf import settings

from workflow.base_matrix import BASE_STATUSES
from workflow.models import CHANNELS

BASE_STATUS_CODES = [code for code, _ in BASE_STATUSES]
CHANNEL_CODES = [code for code, _ in CHANNELS]

ERROR_CODES = [
    'no_workflow', 'inactive', 'channel_forbidden', 'wrong_status', 'contract_violation',
    'guard_failed', 'invalid_form', 'config_error', 'no_transition', 'ambiguous',
]


def _str(description, **extra):
    return {'type': 'string', 'description': description, **extra}


def openapi_schema():
    return {
        'openapi': '3.0.3',
        'info': {
            'title': 'cat-workflow API',
            'version': '1.0.0',
            'description': (
                'Смена статуса заявки по настроенному бизнес-процессу проекта.\n\n'
                'Авторизация: заголовок `X-Api-Key` (для прототипа ключ `{}`) либо сессия сотрудника. '
                'Канал по умолчанию — `api`: переход должен быть разрешён для этого канала в конструкторе.\n\n'
                'Поля формы не фиксированы. Их имена, типы и зависимости отдаёт список переходов '
                '(`form`), и с теми же именами их нужно передать при смене статуса.'
            ).format(settings.DEMO_API_KEY),
        },
        'servers': [{'url': '/'}],
        'tags': [{'name': 'Заявки', 'description': 'Переходы заявки по матрице проекта'}],
        'security': [{'ApiKey': []}, {}],
        'paths': {
            '/api/applications/{pk}/transitions/': {
                'get': _transitions(),
            },
            '/api/applications/{pk}/status/': {
                'post': _change_status(),
            },
        },
        'components': {
            'securitySchemes': {
                'ApiKey': {'type': 'apiKey', 'in': 'header', 'name': 'X-Api-Key'},
            },
            'parameters': {
                'pk': {'name': 'pk', 'in': 'path', 'required': True, 'description': 'Номер заявки',
                       'schema': {'type': 'integer'}},
            },
            'schemas': _schemas(),
        },
    }


def _transitions():
    return {
        'tags': ['Заявки'],
        'operationId': 'listTransitions',
        'summary': 'Доступные переходы',
        'description': 'Переходы из текущего статуса заявки и схемы их форм. '
                       'Заблокированные условием переходы тоже возвращаются: `allowed: false` и текст в `reasons`.',
        'parameters': [
            {'$ref': '#/components/parameters/pk'},
            {'name': 'channel', 'in': 'query', 'schema': {'type': 'string', 'enum': CHANNEL_CODES, 'default': 'api'},
             'description': 'Откуда запрашивается переход. По умолчанию api.'},
        ],
        'responses': {
            '200': _json('Переходы и схемы форм', {'$ref': '#/components/schemas/TransitionsResponse'}),
            '401': _json('Нет ключа и нет сессии сотрудника', {'$ref': '#/components/schemas/Unauthorized'}),
            '404': {'description': 'Заявка не найдена'},
        },
    }


def _change_status():
    form_fields = {
        'flow': _str('Поток: id потока курса заявки'),
        'enter_exam_date': _str('Дата вступительного испытания, ГГГГ-ММ-ДД'),
        'reason_choice': _str('Причина: значение из справочника формы'),
        'reason_detail': _str('Уточнение причины, зависит от reason_choice'),
        'reason_text': _str('Комментарий к причине'),
        'bas_declined_date': _str('Дата решения об отклонении, ГГГГ-ММ-ДД'),
        'reason_file': {'type': 'string', 'format': 'binary', 'description': 'Документ-основание'},
        'reason_file_ds': {'type': 'string', 'format': 'binary', 'description': 'Подпись документа (ЭП)'},
        'accept_date': _str('Дата зачисления, ГГГГ-ММ-ДД'),
        'admission_order_date': _str('Дата приказа о зачислении, ГГГГ-ММ-ДД'),
        'admission_order_number': _str('Номер приказа о зачислении'),
        'employer_pays': _str('Оплачивает работодатель: true или false'),
        'employer_name': _str('Наименование работодателя'),
        'employer_ogrn': _str('ОГРН работодателя'),
        'expulsion_date': _str('Дата отчисления, ГГГГ-ММ-ДД'),
        'expulsion_order_date': _str('Дата приказа об отчислении, ГГГГ-ММ-ДД'),
        'expulsion_order_number': _str('Номер приказа об отчислении'),
        'final_project_name': _str('Название итогового проекта'),
        'difficulty_level': _str('Уровень'),
    }
    body = {
        'type': 'object',
        'description': 'Нужен `status` или `transition`. Остальные поля — из схемы формы перехода; '
                       'ниже перечислены поля, которые умеет прототип.',
        'properties': {
            'status': _str('Базовый статус, в который нужно перевести заявку', enum=BASE_STATUS_CODES),
            'transition': _str('Код перехода. Альтернатива status, если в базовый статус ведёт несколько переходов'),
            **form_fields,
        },
        'additionalProperties': {'type': 'string',
                                 'description': 'Любое другое поле формы по имени из схемы перехода'},
    }
    return {
        'tags': ['Заявки'],
        'operationId': 'changeStatus',
        'summary': 'Сменить статус',
        'description': 'Выполняет переход. `status` ищет единственный переход канала api в указанный базовый статус, '
                       '`transition` выбирает переход по коду. Данные передаются формой: файлы — через multipart.',
        'parameters': [{'$ref': '#/components/parameters/pk'}],
        'requestBody': {
            'required': True,
            'content': {
                'multipart/form-data': {'schema': body, 'examples': _examples()},
                'application/x-www-form-urlencoded': {'schema': body, 'examples': _examples()},
            },
        },
        'responses': {
            '200': _json('Статус изменён', {'$ref': '#/components/schemas/StatusChanged'}),
            '400': _json('Переход отклонён или не хватает параметра',
                         {'$ref': '#/components/schemas/ChangeStatusError'}),
            '401': _json('Нет ключа и нет сессии сотрудника', {'$ref': '#/components/schemas/Unauthorized'}),
            '404': {'description': 'Заявка не найдена'},
        },
    }


def _examples():
    return {
        'accept': {
            'summary': 'Зачислить по базовому статусу',
            'value': {'status': 'accepted', 'accept_date': '2026-11-01', 'admission_order_date': '2026-10-30',
                      'admission_order_number': '3'},
        },
        'decline': {
            'summary': 'Отклонить по коду перехода',
            'value': {'transition': 'decline', 'reason_choice': 'no_places', 'bas_declined_date': '2026-10-29'},
        },
    }


def _json(description, schema):
    return {'description': description, 'content': {'application/json': {'schema': schema}}}


def _schemas():
    choice = {
        'type': 'object',
        'required': ['value', 'label'],
        'properties': {
            'value': {'type': 'string'},
            'label': {'type': 'string'},
            'parents': {'type': 'array', 'items': {'type': 'string'},
                        'description': 'Значения родительского списка, при которых вариант доступен'},
        },
    }
    form_field = {
        'type': 'object',
        'required': ['name', 'label', 'type', 'required'],
        'properties': {
            'name': _str('Имя параметра в запросе смены статуса'),
            'label': {'type': 'string'},
            'type': _str('Класс поля Django: CharField, DateField, BooleanField, ChoiceField, ModelChoiceField, FileField'),
            'required': {'type': 'boolean'},
            'help_text': {'type': 'string'},
            'widget': {'type': 'string', 'enum': ['select', 'autocomplete'], 'description': 'Только у списков'},
            'choices': {'type': 'array', 'items': choice},
            'depends_on': _str('Имя списка, от которого зависят варианты'),
            'initial': {'type': 'string'},
            'visible_if': {
                'type': 'object',
                'description': 'Поле нужно заполнять, только если параметр равен значению',
                'properties': {'param': {'type': 'string'}, 'value': {'type': 'string'}},
            },
        },
    }
    transition = {
        'type': 'object',
        'required': ['code', 'label', 'to_status', 'to_base_status', 'allowed', 'reasons', 'form'],
        'properties': {
            'code': _str('Код перехода, его можно передать как transition'),
            'label': _str('Текст кнопки'),
            'to_status': _str('Код проектного статуса'),
            'to_base_status': {'type': 'string', 'enum': BASE_STATUS_CODES, 'description': 'Базовый статус'},
            'allowed': {'type': 'boolean', 'description': 'false — условие перехода не выполнено'},
            'reasons': {'type': 'array', 'items': {'type': 'string'}},
            'form': {'type': 'array', 'nullable': True, 'items': form_field,
                     'description': 'null — у перехода нет формы'},
        },
    }
    field_error = {
        'type': 'object',
        'additionalProperties': {
            'type': 'array',
            'items': {'type': 'object', 'properties': {'message': {'type': 'string'}, 'code': {'type': 'string'}}},
        },
        'description': 'Ошибки по именам полей формы',
    }
    return {
        'FormField': form_field,
        'Transition': transition,
        'TransitionsResponse': {
            'type': 'object',
            'required': ['application', 'status', 'workflow_status', 'transitions'],
            'properties': {
                'application': {'type': 'integer'},
                'status': {'type': 'string', 'enum': BASE_STATUS_CODES, 'description': 'Текущий базовый статус'},
                'workflow_status': {'type': 'string', 'nullable': True, 'description': 'Код проектного статуса'},
                'transitions': {'type': 'array', 'items': transition},
            },
        },
        'StatusChanged': {
            'type': 'object',
            'required': ['success', 'status', 'workflow_status'],
            'properties': {
                'success': {'type': 'boolean'},
                'status': {'type': 'string', 'enum': BASE_STATUS_CODES},
                'workflow_status': _str('Код проектного статуса после перехода'),
            },
        },
        'ChangeStatusError': {
            'oneOf': [
                {'type': 'object', 'required': ['error', 'code'], 'properties': {
                    'error': {'type': 'string'},
                    'code': {'type': 'string', 'enum': ERROR_CODES},
                    'fields': field_error,
                }},
                {'type': 'object', 'required': ['status'], 'properties': {
                    'status': {'type': 'string', 'example': 'required parameter'},
                }, 'description': 'Не передан ни status, ни transition'},
            ],
        },
        'Unauthorized': {
            'type': 'object',
            'properties': {'error': {'type': 'string', 'example': 'unauthorized'}},
        },
    }
