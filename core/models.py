"""Упрощённые копии моделей rall: только поля, нужные для демонстрации конструктора."""
from django.conf import settings
from django.db import models

from workflow.base_matrix import BASE_STATUSES, REVIEW


class Project(models.Model):
    title = models.CharField('Название', max_length=255)
    is_expelling_prohibited = models.BooleanField('Запретить отчисления', default=False)

    class Meta:
        verbose_name = 'Проект'
        verbose_name_plural = 'Проекты'

    def __str__(self):
        return '{}-{}'.format(self.id, self.title)


class Course(models.Model):
    title = models.CharField('Название', max_length=255)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='courses')

    class Meta:
        verbose_name = 'Курс'
        verbose_name_plural = 'Курсы'

    def __str__(self):
        return self.title


class LearningFlow(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='flows')
    title = models.CharField('Название', max_length=255)
    is_active = models.BooleanField('Активен', default=True)
    is_filled = models.BooleanField('Заполнен (закрыт)', default=False)

    class Meta:
        verbose_name = 'Поток'
        verbose_name_plural = 'Потоки'

    def __str__(self):
        return self.title


class OrgDocument(models.Model):
    title = models.CharField('Название', max_length=255)
    file = models.FileField('Файл', upload_to='documents')
    signature = models.FileField('Подпись (ЭП)', upload_to='documents', blank=True)
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Документ'
        verbose_name_plural = 'Документы'

    def __str__(self):
        return self.title


class Transfert(models.Model):
    STATUS_BOOKED = 'booked'
    STATUS_ACTIVATED = 'activated'
    STATUS_CONDITIONALLY_ACTIVATED = 'conditionally_activated'
    STATUSES = (
        (STATUS_BOOKED, 'Забронирован'),
        (STATUS_ACTIVATED, 'Активирован'),
        (STATUS_CONDITIONALLY_ACTIVATED, 'Активирован условно'),
    )

    status = models.CharField('Статус', max_length=30, choices=STATUSES, default=STATUS_BOOKED)
    enter_exam_date = models.DateField('Дата вступительного испытания', null=True, blank=True)
    accept_date = models.DateField('Дата зачисления', null=True, blank=True)
    admission_order_date = models.DateField('Дата приказа о зачислении', null=True, blank=True)
    admission_order_number = models.CharField('Номер приказа о зачислении', max_length=255, blank=True, default='')
    expulsion_date = models.DateField('Дата отчисления', null=True, blank=True)
    expulsion_order_date = models.DateField('Дата приказа об отчислении', null=True, blank=True)
    expulsion_order_number = models.CharField('Номер приказа об отчислении', max_length=255, blank=True, default='')
    employer_pays = models.BooleanField('Оплачивает работодатель', default=False)
    employer_name = models.CharField('Наименование работодателя', max_length=255, blank=True, default='')
    employer_ogrn = models.CharField('ОГРН работодателя', max_length=15, blank=True, default='')

    class Meta:
        verbose_name = 'Трансферт'
        verbose_name_plural = 'Трансферты'

    def __str__(self):
        return 'Трансферт #{} ({})'.format(self.pk, self.get_status_display())


class CourseUserStatus(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    course = models.ForeignKey(Course, on_delete=models.CASCADE)
    is_active = models.BooleanField(default=True)
    dt_finish = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Запись на курс'
        verbose_name_plural = 'Записи на курсы'
        unique_together = ('user', 'course')


class TicketApplication(models.Model):
    DIFFICULTY_LEVELS = (
        ('intermediate', 'Начальный'),
        ('elementary', 'Базовый'),
        ('advanced', 'Продвинутый'),
    )

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='applications',
                                verbose_name='Проект')
    course = models.ForeignKey(Course, on_delete=models.CASCADE, verbose_name='Курс')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='ticket_applications',
                             verbose_name='Слушатель')
    applied_at = models.DateTimeField('Дата подачи', auto_now_add=True)

    status = models.CharField('Базовый статус', max_length=20, choices=BASE_STATUSES, default=REVIEW)
    _status = models.CharField(max_length=60)
    workflow_status = models.ForeignKey('workflow.WorkflowStatus', on_delete=models.PROTECT, null=True, blank=True,
                                        related_name='applications', verbose_name='Статус проекта')
    reviewed_at = models.DateTimeField('Когда вынесено решение', null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+', verbose_name='Кем вынесено решение')

    reason_choice = models.CharField('Причина', max_length=255, blank=True, default='')
    reason_detail = models.CharField('Уточнение причины', max_length=255, blank=True, default='')
    reason_text = models.TextField('Комментарий к причине', blank=True, default='')
    reason_document = models.ForeignKey(OrgDocument, on_delete=models.SET_NULL, null=True, blank=True,
                                        verbose_name='Подтверждение причины')
    bas_declined_date = models.DateField('Дата решения об отклонении', null=True, blank=True)

    flow = models.ForeignKey(LearningFlow, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Поток')
    transfert = models.OneToOneField(Transfert, on_delete=models.SET_NULL, null=True, blank=True)
    difficulty_level = models.CharField('Уровень', max_length=20, choices=DIFFICULTY_LEVELS, blank=True, default='')
    final_project_name = models.CharField('Название итогового проекта', max_length=255, blank=True, default='')
    dt_finished = models.DateTimeField('Дата окончания обучения', null=True, blank=True)

    class Meta:
        verbose_name = 'Заявка на курс'
        verbose_name_plural = 'Заявки на курсы'
        unique_together = ('user', 'course', '_status')

    def __str__(self):
        return 'Заявка #{}'.format(self.pk)

    @property
    def status_title(self):
        from workflow.engine import display_status
        return display_status(self)
