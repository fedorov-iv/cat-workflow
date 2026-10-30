import json

import nested_admin
from django import forms
from django.apps import apps
from django.conf import settings
from django.contrib import admin, messages
from django.http import HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import escape, format_html, format_html_join
from django.utils.safestring import mark_safe

from .graph import to_mermaid
from .models import (CHANNELS, ChoiceItem, ChoiceSet, TransitionEffect, TransitionForm, TransitionFormField,
                     TransitionGuard, TransitionLog, Workflow, WorkflowStatus, WorkflowTransition)
from .registry import bindings, effects, guards
from .serialization import clone_workflow, export_workflow, import_workflow
from .validation import ERROR, has_errors, validate_form, validate_workflow


def _params_help(registry):
    return mark_safe('<br>'.join(escape(line) for line in registry.describe()))


class GuardInlineForm(forms.ModelForm):
    code = forms.ChoiceField(label='Условие', choices=guards.choices)

    class Meta:
        model = TransitionGuard
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['params'].help_text = _params_help(guards)


class EffectInlineForm(forms.ModelForm):
    code = forms.ChoiceField(label='Действие', choices=effects.choices)

    class Meta:
        model = TransitionEffect
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['params'].help_text = _params_help(effects)


class TransitionAdminForm(forms.ModelForm):
    channels = forms.MultipleChoiceField(label='Каналы', choices=CHANNELS, widget=forms.CheckboxSelectMultiple,
                                         required=False)

    class Meta:
        model = WorkflowTransition
        fields = '__all__'


class FormFieldInlineForm(forms.ModelForm):
    binding = forms.ChoiceField(label='Поле модели', choices=bindings.choices)

    class Meta:
        model = TransitionFormField
        fields = '__all__'


class WorkflowScopedMixin:
    """Ограничивает выбор статусов статусами редактируемого процесса."""

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.remote_field.model is WorkflowStatus:
            workflow = getattr(request, '_workflow_obj', None)
            kwargs['queryset'] = (WorkflowStatus.objects.filter(workflow=workflow)
                                  if workflow else WorkflowStatus.objects.none())
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class StatusInline(nested_admin.NestedTabularInline):
    model = WorkflowStatus
    extra = 0
    fields = ('order', 'code', 'title', 'base_status', 'is_final')
    sortable_field_name = 'order'


class GuardInline(nested_admin.NestedTabularInline):
    model = TransitionGuard
    form = GuardInlineForm
    extra = 0
    fields = ('order', 'code', 'params', 'error_message')
    sortable_field_name = 'order'


class EffectInline(nested_admin.NestedTabularInline):
    model = TransitionEffect
    form = EffectInlineForm
    extra = 0
    fields = ('order', 'code', 'params')
    sortable_field_name = 'order'


class TransitionInline(WorkflowScopedMixin, nested_admin.NestedStackedInline):
    model = WorkflowTransition
    form = TransitionAdminForm
    extra = 0
    inlines = [GuardInline, EffectInline]
    sortable_field_name = 'order'
    fieldsets = (
        (None, {'fields': (('from_status', 'to_status'), ('code', 'is_active', 'order'),
                           ('button_label', 'button_style'), 'form', 'channels', 'confirm_text')}),
    )


class ImportForm(forms.Form):
    project = forms.ModelChoiceField(label='Проект', queryset=None)
    file = forms.FileField(label='JSON-файл')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['project'].queryset = _projects_without_workflow()


class CloneForm(forms.Form):
    project = forms.ModelChoiceField(label='В проект', queryset=None)
    title = forms.CharField(label='Название', required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['project'].queryset = _projects_without_workflow()


def _issues_html(issues):
    if not issues:
        return 'Ошибок и предупреждений нет.'
    return format_html('<ul style="margin:0">{}</ul>', format_html_join(
        '', '<li style="color:{}">{}</li>',
        (('#ba2121' if i.level == ERROR else '#9a6700', str(i)) for i in issues)))


def _projects_without_workflow():
    return apps.get_model(settings.WORKFLOW_PROJECT_MODEL).objects.filter(workflow__isnull=True)


@admin.register(Workflow)
class WorkflowAdmin(WorkflowScopedMixin, nested_admin.NestedModelAdmin):
    list_display = ('title', 'project', 'is_active', 'issues_summary', 'updated_at')
    list_filter = ('is_active',)
    inlines = [StatusInline, TransitionInline]
    readonly_fields = ('issues_report', 'graph')
    actions = ('export_json', 'clone')
    change_list_template = 'admin/workflow/workflow/change_list.html'
    fieldsets = (
        (None, {'fields': ('project', 'title', 'initial_status', 'is_active')}),
        ('Проверка конфигурации', {'fields': ('issues_report',)}),
        ('Граф переходов', {'fields': ('graph',),
                            'description': 'Звёздочкой отмечены переходы с формой. Сохраните, чтобы обновить.'}),
    )

    def get_object(self, request, object_id, from_field=None):
        obj = super().get_object(request, object_id, from_field)
        request._workflow_obj = obj
        return obj

    @admin.display(description='Проблемы')
    def issues_summary(self, obj):
        issues = validate_workflow(obj)
        errors = sum(1 for i in issues if i.level == ERROR)
        warnings = len(issues) - errors
        if not issues:
            return 'нет'
        return 'ошибок: {}, предупреждений: {}'.format(errors, warnings)

    @admin.display(description='Результат')
    def issues_report(self, obj):
        if not obj or not obj.pk:
            return 'Сохраните процесс, чтобы увидеть проверку.'
        return _issues_html(validate_workflow(obj))

    @admin.display(description='Граф')
    def graph(self, obj):
        if not obj or not obj.pk:
            return '-'
        return format_html(
            '<div style="overflow-x:auto;max-width:calc(100vw - 420px)">'
            '<pre class="mermaid" style="background:#fff;margin:0">{}</pre></div>'
            '<script type="module">'
            'import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";'
            'mermaid.initialize({{startOnLoad: true, flowchart: {{useMaxWidth: false}}}});'
            '</script>',
            to_mermaid(obj),
        )

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        workflow = form.instance
        issues = validate_workflow(workflow)
        if workflow.is_active and has_errors(issues):
            workflow.is_active = False
            workflow.save(update_fields=['is_active'])
            self.message_user(request, 'Процесс не активирован: в конфигурации есть ошибки.', messages.ERROR)
        for issue in issues:
            self.message_user(request, str(issue), messages.ERROR if issue.level == ERROR else messages.WARNING)

    @admin.action(description='Экспортировать в JSON')
    def export_json(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, 'Выберите один процесс', messages.ERROR)
            return None
        workflow = queryset.get()
        response = HttpResponse(json.dumps(export_workflow(workflow), ensure_ascii=False, indent=2),
                                content_type='application/json')
        response['Content-Disposition'] = 'attachment; filename="workflow-project-{}.json"'.format(
            workflow.project_id)
        return response

    @admin.action(description='Клонировать в другой проект')
    def clone(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, 'Выберите один процесс', messages.ERROR)
            return None
        workflow = queryset.get()
        form = CloneForm(request.POST if 'apply' in request.POST else None)
        if form.is_valid():
            new = clone_workflow(workflow, form.cleaned_data['project'], form.cleaned_data['title'] or None)
            self.message_user(request, 'Создан неактивный процесс «{}»'.format(new.title))
            return HttpResponseRedirect(reverse('admin:workflow_workflow_change', args=[new.pk]))
        return TemplateResponse(request, 'admin/workflow/workflow/clone.html', {
            **self.admin_site.each_context(request),
            'title': 'Клонирование процесса «{}»'.format(workflow.title),
            'form': form, 'workflow': workflow, 'opts': self.model._meta,
        })

    def get_urls(self):
        return [
            path('import/', self.admin_site.admin_view(self.import_view), name='workflow_workflow_import'),
        ] + super().get_urls()

    def import_view(self, request):
        form = ImportForm(request.POST or None, request.FILES or None)
        if form.is_valid():
            try:
                data = json.loads(form.cleaned_data['file'].read().decode('utf-8'))
                workflow = import_workflow(data, form.cleaned_data['project'])
            except (ValueError, KeyError) as e:
                form.add_error(None, 'Не удалось импортировать: {}'.format(e))
            else:
                self.message_user(request, 'Импортирован неактивный процесс «{}»'.format(workflow.title))
                return HttpResponseRedirect(reverse('admin:workflow_workflow_change', args=[workflow.pk]))
        return TemplateResponse(request, 'admin/workflow/workflow/import.html', {
            **self.admin_site.each_context(request),
            'title': 'Импорт бизнес-процесса', 'form': form, 'opts': self.model._meta,
        })


class FormFieldInline(nested_admin.NestedStackedInline):
    model = TransitionFormField
    form = FormFieldInlineForm
    extra = 0
    sortable_field_name = 'order'
    fieldsets = (
        (None, {'fields': (('binding', 'order'), ('label', 'param_name', 'required'), 'help_text')}),
        ('Список', {'fields': (('choice_set', 'widget', 'depends_on'),)}),
        ('Видимость', {'fields': (('visible_if_param', 'visible_if_value'),)}),
    )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'depends_on':
            form_def = getattr(request, '_form_obj', None)
            kwargs['queryset'] = (TransitionFormField.objects.filter(form=form_def)
                                  if form_def else TransitionFormField.objects.none())
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(TransitionForm)
class TransitionFormAdmin(nested_admin.NestedModelAdmin):
    list_display = ('title', 'code', 'used_in')
    inlines = [FormFieldInline]
    readonly_fields = ('issues_report',)
    fieldsets = (
        (None, {'fields': ('code', 'title', 'description')}),
        ('Проверка формы', {'fields': ('issues_report',),
                            'description': 'Зависимость от списка можно выбрать после сохранения нового поля.'}),
    )

    def get_object(self, request, object_id, from_field=None):
        obj = super().get_object(request, object_id, from_field)
        request._form_obj = obj
        return obj

    @admin.display(description='Результат')
    def issues_report(self, obj):
        if not obj or not obj.pk:
            return 'Сохраните форму, чтобы увидеть проверку.'
        return _issues_html(validate_form(obj))

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        for issue in validate_form(form.instance):
            self.message_user(request, str(issue), messages.ERROR if issue.level == ERROR else messages.WARNING)

    @admin.display(description='Используется в')
    def used_in(self, obj):
        return ', '.join(sorted({str(t.workflow.project) for t in obj.transitions.select_related('workflow__project')}))


class ChoiceItemInline(admin.TabularInline):
    model = ChoiceItem
    extra = 0
    fields = ('order', 'value', 'label', 'parent_values', 'is_active')


@admin.register(ChoiceSet)
class ChoiceSetAdmin(admin.ModelAdmin):
    list_display = ('title', 'code')
    inlines = [ChoiceItemInline]


@admin.register(TransitionLog)
class TransitionLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'application', 'from_status', 'to_status', 'actor', 'channel')
    list_filter = ('channel', 'to_base_status')
    search_fields = ('application__id',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
