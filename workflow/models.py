from django.conf import settings
from django.db import models

from .base_matrix import BASE_STATUSES, base_status_title

CHANNEL_ARM = 'arm'
CHANNEL_API = 'api'
CHANNEL_SYSTEM = 'system'
CHANNEL_USER = 'user'

CHANNELS = (
    (CHANNEL_ARM, 'АРМ (кнопка)'),
    (CHANNEL_API, 'API платформ'),
    (CHANNEL_SYSTEM, 'Система (задачи, интеграции)'),
    (CHANNEL_USER, 'Слушатель (ЛК)'),
)


def default_channels():
    return [CHANNEL_ARM]


class Workflow(models.Model):
    project = models.OneToOneField(settings.WORKFLOW_PROJECT_MODEL, on_delete=models.CASCADE,
                                   related_name='workflow', verbose_name='Проект')
    title = models.CharField('Название', max_length=255)
    is_active = models.BooleanField(
        'Активен', default=False,
        help_text='Пока выключен, заявки проекта обрабатываются старым кодом. '
                  'Включить можно только если в конфигурации нет ошибок.',
    )
    initial_status = models.ForeignKey('WorkflowStatus', on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+', verbose_name='Начальный статус')
    updated_at = models.DateTimeField('Изменён', auto_now=True)

    class Meta:
        verbose_name = 'Бизнес-процесс проекта'
        verbose_name_plural = 'Бизнес-процессы проектов'

    def __str__(self):
        return '{} ({})'.format(self.title, self.project)


class WorkflowStatus(models.Model):
    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name='statuses')
    code = models.SlugField('Код', max_length=50)
    title = models.CharField('Название', max_length=255)
    base_status = models.CharField('Базовый статус', max_length=20, choices=BASE_STATUSES,
                                   help_text='Что видят интеграции (Kafka, 1С, ЛК).')
    is_final = models.BooleanField('Конечный', default=False)
    order = models.PositiveIntegerField('Порядок', default=0)

    class Meta:
        verbose_name = 'Статус'
        verbose_name_plural = 'Статусы'
        ordering = ('order', 'id')
        unique_together = ('workflow', 'code')

    def __str__(self):
        return '{} [{}]'.format(self.title, base_status_title(self.base_status))


class ChoiceSet(models.Model):
    code = models.SlugField('Код', max_length=50, unique=True)
    title = models.CharField('Название', max_length=255)

    class Meta:
        verbose_name = 'Справочник значений'
        verbose_name_plural = 'Справочники значений'

    def __str__(self):
        return self.title

    def as_choices(self):
        return [(item.value, item.label) for item in self.items.filter(is_active=True).order_by('order', 'id')]


class ChoiceItem(models.Model):
    choice_set = models.ForeignKey(ChoiceSet, on_delete=models.CASCADE, related_name='items')
    value = models.CharField('Значение', max_length=255)
    label = models.CharField('Подпись', max_length=500)
    order = models.PositiveIntegerField('Порядок', default=0)
    is_active = models.BooleanField('Активно', default=True)
    parent_values = models.CharField(
        'При значениях родителя', max_length=500, blank=True, default='',
        help_text='Для зависимых списков: значения родительского списка через запятую, '
                  'при которых показывается это значение. Пусто — показывается всегда.',
    )

    class Meta:
        verbose_name = 'Значение'
        verbose_name_plural = 'Значения'
        ordering = ('order', 'id')
        unique_together = ('choice_set', 'value')

    def __str__(self):
        return self.label

    def parent_value_list(self):
        return [v.strip() for v in self.parent_values.split(',') if v.strip()]


class TransitionForm(models.Model):
    code = models.SlugField('Код', max_length=100, unique=True)
    title = models.CharField('Заголовок формы', max_length=255)
    description = models.TextField('Описание для сотрудника', blank=True, default='')

    class Meta:
        verbose_name = 'Форма перехода'
        verbose_name_plural = 'Формы переходов'

    def __str__(self):
        return '{} [{}]'.format(self.title, self.code)


class TransitionFormField(models.Model):
    WIDGET_SELECT = 'select'
    WIDGET_AUTOCOMPLETE = 'autocomplete'
    WIDGET_CHOICES = (
        (WIDGET_SELECT, 'Простой список'),
        (WIDGET_AUTOCOMPLETE, 'Список с автокомплитом'),
    )

    form = models.ForeignKey(TransitionForm, on_delete=models.CASCADE, related_name='field_configs')
    binding = models.CharField('Поле модели', max_length=100,
                               help_text='Куда сохраняется значение. Список задаётся в коде (реестр bindings).')
    param_name = models.CharField('Имя параметра', max_length=100, blank=True, default='',
                                  help_text='Имя в POST/API. Пусто — имя по умолчанию из привязки.')
    label = models.CharField('Подпись', max_length=255, blank=True, default='')
    help_text = models.CharField('Подсказка', max_length=500, blank=True, default='')
    required = models.BooleanField('Обязательное', default=True)
    order = models.PositiveIntegerField('Порядок', default=0)
    choice_set = models.ForeignKey(ChoiceSet, on_delete=models.PROTECT, null=True, blank=True,
                                   verbose_name='Справочник значений')
    widget = models.CharField('Вид списка', max_length=20, choices=WIDGET_CHOICES, default=WIDGET_SELECT,
                              help_text='Только для полей-списков.')
    depends_on = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='dependents',
        verbose_name='Зависит от списка',
        help_text='Варианты сужаются по значению выбранного списка этой же формы. Для справочника связь задаётся '
                  'в его значениях («При значениях родителя»), для списков из моделей — по внешнему ключу.',
    )
    visible_if_param = models.CharField('Показывать, если параметр', max_length=100, blank=True, default='')
    visible_if_value = models.CharField('равен', max_length=255, blank=True, default='',
                                        help_text='Для флажков: true / false.')

    class Meta:
        verbose_name = 'Поле формы'
        verbose_name_plural = 'Поля формы'
        ordering = ('order', 'id')

    def __str__(self):
        return '{} ({})'.format(self.label, self.binding) if self.label else self.binding


class WorkflowTransition(models.Model):
    STYLE_CHOICES = (
        ('primary', 'Основная'),
        ('success', 'Зелёная'),
        ('danger', 'Красная'),
        ('secondary', 'Серая'),
    )

    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name='transitions')
    code = models.SlugField('Код', max_length=50,
                            help_text='Уникален в рамках исходного статуса. Используется в API и логах.')
    from_status = models.ForeignKey(WorkflowStatus, on_delete=models.CASCADE, related_name='outgoing',
                                    verbose_name='Из статуса')
    to_status = models.ForeignKey(WorkflowStatus, on_delete=models.CASCADE, related_name='incoming',
                                  verbose_name='В статус')
    button_label = models.CharField('Текст кнопки', max_length=100)
    button_style = models.CharField('Стиль кнопки', max_length=20, choices=STYLE_CHOICES, default='primary')
    confirm_text = models.TextField('Текст подтверждения', blank=True, default='')
    channels = models.JSONField('Каналы', default=default_channels,
                                help_text='Откуда разрешён переход.')
    form = models.ForeignKey(TransitionForm, on_delete=models.PROTECT, null=True, blank=True,
                             related_name='transitions', verbose_name='Форма')
    order = models.PositiveIntegerField('Порядок', default=0)
    is_active = models.BooleanField('Активен', default=True)

    class Meta:
        verbose_name = 'Переход'
        verbose_name_plural = 'Переходы'
        ordering = ('order', 'id')
        unique_together = ('workflow', 'from_status', 'code')

    def __str__(self):
        return '{}: {} → {}'.format(self.button_label, self.from_status.title, self.to_status.title)


class TransitionGuard(models.Model):
    transition = models.ForeignKey(WorkflowTransition, on_delete=models.CASCADE, related_name='guards')
    code = models.CharField('Условие', max_length=100)
    params = models.JSONField('Параметры', default=dict, blank=True)
    error_message = models.CharField('Сообщение при отказе', max_length=500, blank=True, default='',
                                     help_text='Пусто — сообщение по умолчанию.')
    order = models.PositiveIntegerField('Порядок', default=0)

    class Meta:
        verbose_name = 'Условие перехода'
        verbose_name_plural = 'Условия перехода'
        ordering = ('order', 'id')

    def __str__(self):
        return self.code


class TransitionEffect(models.Model):
    transition = models.ForeignKey(WorkflowTransition, on_delete=models.CASCADE, related_name='effects')
    code = models.CharField('Действие', max_length=100)
    params = models.JSONField('Параметры', default=dict, blank=True)
    order = models.PositiveIntegerField('Порядок', default=0)

    class Meta:
        verbose_name = 'Действие после перехода'
        verbose_name_plural = 'Действия после перехода'
        ordering = ('order', 'id')

    def __str__(self):
        return self.code


class TransitionLog(models.Model):
    application = models.ForeignKey(settings.WORKFLOW_SUBJECT_MODEL, on_delete=models.CASCADE,
                                    related_name='workflow_log', verbose_name='Заявка')
    transition = models.ForeignKey(WorkflowTransition, on_delete=models.SET_NULL, null=True,
                                   related_name='+', verbose_name='Переход')
    from_status = models.ForeignKey(WorkflowStatus, on_delete=models.SET_NULL, null=True, related_name='+',
                                    verbose_name='Из статуса')
    to_status = models.ForeignKey(WorkflowStatus, on_delete=models.SET_NULL, null=True, related_name='+',
                                  verbose_name='В статус')
    from_base_status = models.CharField('Из базового', max_length=20)
    to_base_status = models.CharField('В базовый', max_length=20)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='+', verbose_name='Кто')
    channel = models.CharField('Канал', max_length=20, choices=CHANNELS)
    written_bindings = models.JSONField('Заполненные поля', default=list,
                                        help_text='Только имена привязок; значения хранятся в полях моделей.')
    created_at = models.DateTimeField('Когда', auto_now_add=True)

    class Meta:
        verbose_name = 'Журнал переходов'
        verbose_name_plural = 'Журнал переходов'
        ordering = ('-created_at', '-id')
