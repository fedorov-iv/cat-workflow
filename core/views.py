from dataclasses import dataclass, field
from typing import Callable

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from workflow import engine
from workflow.forms import form_schema
from workflow.models import CHANNEL_API, CHANNEL_ARM, WorkflowTransition

from .models import Course, LearningFlow, Project, TicketApplication
from .openapi import openapi_schema


def _with_actions(applications, user):
    rows = []
    for application in applications:
        rows.append({
            'application': application,
            'has_workflow': engine.get_workflow(application) is not None,
            'options': engine.available_transitions(application, user, CHANNEL_ARM, include_blocked=True),
        })
    return rows


def _int_param(request, name):
    value = request.GET.get(name, '')
    return int(value) if value.isdigit() else None


def _user_label(user):
    full_name = user.get_full_name()
    return '{} ({})'.format(full_name, user.username) if full_name else user.username


@dataclass(frozen=True)
class ListFilter:
    label: str
    lookup: str
    source: Callable
    search_fields: tuple
    option_label: Callable
    # Сужение вариантов по уже выбранным фильтрам: {GET-параметр: lookup}.
    depends: dict = field(default_factory=dict)


PAGE_SIZE = 20

LIST_FILTERS = {
    'project': ListFilter(
        'Проект', 'project_id', lambda: Project.objects.order_by('id'), ('title',), str),
    'course': ListFilter(
        'Курс', 'course_id', lambda: Course.objects.select_related('project').order_by('title'),
        ('title', 'project__title'), lambda c: '{} — {}'.format(c.title, c.project.title),
        depends={'project': 'project_id'}),
    'flow': ListFilter(
        'Поток', 'flow_id', lambda: LearningFlow.objects.select_related('course').order_by('course__title', 'title'),
        ('title', 'course__title'), lambda f: '{} — {}'.format(f.title, f.course.title),
        depends={'project': 'course__project_id', 'course': 'course_id'}),
    'user': ListFilter(
        'Слушатель', 'user_id',
        lambda: get_user_model().objects.filter(ticket_applications__isnull=False).order_by('username'),
        ('username', 'first_name', 'last_name', 'email'), _user_label,
        depends={'project': 'ticket_applications__project_id', 'course': 'ticket_applications__course_id'}),
}


@staff_member_required
def application_list(request):
    applications = (TicketApplication.objects
                    .select_related('project', 'course', 'user', 'workflow_status', 'transfert', 'flow')
                    .order_by('project_id', 'id'))
    filters = []
    for name, spec in LIST_FILTERS.items():
        value = _int_param(request, name)
        selected = spec.source().filter(pk=value).first() if value else None
        if selected:
            applications = applications.filter(**{spec.lookup: value})
        filters.append({
            'name': name,
            'label': spec.label,
            'value': selected.pk if selected else '',
            'text': spec.option_label(selected) if selected else '',
            'url': reverse('arm:filter-options', kwargs={'kind': name}),
        })
    page = Paginator(applications, PAGE_SIZE).get_page(_int_param(request, 'page') or 1)
    return render(request, 'arm/application_list.html', {
        'rows': _with_actions(page.object_list, request.user),
        'page': page,
        'filters': filters,
        'is_filtered': any(f['value'] for f in filters),
    })


@staff_member_required
def filter_options(request, kind):
    """Варианты для автокомплита фильтров: ?q=<текст>, сужение по выбранным project / course."""
    spec = LIST_FILTERS.get(kind)
    if spec is None:
        raise Http404
    options = spec.source()
    query = request.GET.get('q', '').strip()
    if query:
        condition = Q()
        for search_field in spec.search_fields:
            condition |= Q(**{'{}__icontains'.format(search_field): query})
        options = options.filter(condition)
    for param, lookup in spec.depends.items():
        value = _int_param(request, param)
        if value:
            options = options.filter(**{lookup: value})
    return JsonResponse({'results': [{'id': obj.pk, 'text': spec.option_label(obj)}
                                     for obj in options.distinct()[:20]]})


@staff_member_required
def application_detail(request, pk):
    application = get_object_or_404(TicketApplication.objects.select_related('project', 'course', 'transfert'), pk=pk)
    return render(request, 'arm/application_detail.html', {
        'row': _with_actions([application], request.user)[0],
        'log': application.workflow_log.select_related('from_status', 'to_status', 'actor', 'transition'),
    })


BOOTSTRAP_WIDGET_CLASSES = (
    (forms.CheckboxInput, 'form-check-input'),
    (forms.Select, 'form-select'),
)


def _bootstrapify(form):
    if form is None:
        return None
    errors = form.errors if form.is_bound else {}
    for name, field in form.fields.items():
        css = next((c for widget_cls, c in BOOTSTRAP_WIDGET_CLASSES if isinstance(field.widget, widget_cls)),
                   'form-control')
        if name in errors:
            css += ' is-invalid'
        field.widget.attrs['class'] = css
    return form


def _safe_next(request, url):
    if url and url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()}):
        return url
    return ''


@staff_member_required
def application_transition(request, pk, transition_id):
    """
    Форма перехода. Для AJAX (модалка в АРМ) отдаёт фрагмент modal-content и JSON с результатом;
    без JS работает как обычная страница.
    """
    application = get_object_or_404(TicketApplication, pk=pk)
    transition = get_object_or_404(WorkflowTransition, pk=transition_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    next_url = _safe_next(request, request.POST.get('next') or request.GET.get('next'))
    detail_url = reverse('arm:application-detail', kwargs={'pk': pk})
    error = None

    if request.method == 'POST':
        try:
            engine.execute(application, transition, request.user, CHANNEL_ARM, request.POST, request.FILES)
        except engine.TransitionError as e:
            if e.form is None:
                if is_ajax:
                    return JsonResponse({'ok': False, 'error': e.message})
                messages.error(request, e.message)
                return redirect(detail_url)
            form, error = e.form, e.message
        else:
            messages.success(request, '«{}»: заявка #{} переведена в статус «{}»'.format(
                transition.button_label, pk, transition.to_status.title))
            if is_ajax:
                return JsonResponse({'ok': True, 'redirect': next_url or detail_url})
            return redirect(next_url or detail_url)
    else:
        form = engine.get_form(transition, application)

    context = {
        'application': application,
        'transition': transition,
        'form': _bootstrapify(form),
        'visibility': form.visibility_rules() if form else {},
        'next': next_url,
        'error': error,
        'in_modal': is_ajax,
        'action_url': request.path,
    }
    if is_ajax:
        html = render_to_string('arm/_transition_modal.html', context, request=request)
        if request.method == 'POST':
            return JsonResponse({'ok': False, 'html': html})
        return HttpResponse(html)
    return render(request, 'arm/transition_form.html', context)


def api_schema(request):
    return JsonResponse(openapi_schema(), json_dumps_params={'ensure_ascii': False})


def _api_authorized(request):
    return (request.user.is_authenticated and request.user.is_staff) or \
        request.headers.get('X-Api-Key') == settings.DEMO_API_KEY


@csrf_exempt
@require_http_methods(['GET'])
def api_transitions(request, pk):
    """Доступные переходы и схемы их форм — то, из чего внешний фронтенд может собрать UI."""
    if not _api_authorized(request):
        return JsonResponse({'error': 'unauthorized'}, status=401)
    application = get_object_or_404(TicketApplication, pk=pk)
    channel = request.GET.get('channel', CHANNEL_API)
    return JsonResponse({
        'application': application.pk,
        'status': application.status,
        'workflow_status': application.workflow_status.code if application.workflow_status else None,
        'transitions': [{
            'code': option.transition.code,
            'label': option.transition.button_label,
            'to_status': option.transition.to_status.code,
            'to_base_status': option.transition.to_status.base_status,
            'allowed': option.allowed,
            'reasons': option.reasons,
            'form': form_schema(engine.get_form(option.transition, application)),
        } for option in engine.available_transitions(application, None, channel, include_blocked=True)],
    })


@csrf_exempt
@require_http_methods(['POST'])
def api_change_status(request, pk):
    """
    Совместимый с текущим API контракт: status=<базовый статус> + параметры формы по их именам
    (accept_date, reason_choice, ...). Альтернатива — transition=<код перехода>.
    """
    if not _api_authorized(request):
        return JsonResponse({'error': 'unauthorized'}, status=401)
    application = get_object_or_404(TicketApplication, pk=pk)
    actor = request.user if request.user.is_authenticated else None
    try:
        if request.POST.get('transition'):
            result = engine.execute_by_code(application, request.POST['transition'], actor, CHANNEL_API,
                                            request.POST, request.FILES)
        elif request.POST.get('status'):
            result = engine.transition_to_base(application, request.POST['status'], actor, CHANNEL_API,
                                               request.POST, request.FILES)
        else:
            return JsonResponse({'status': 'required parameter'}, status=400)
    except engine.TransitionError as e:
        payload = {'error': e.message, 'code': e.code}
        if e.form is not None:
            payload['fields'] = e.form.errors.get_json_data()
        return JsonResponse(payload, status=400)
    return JsonResponse({
        'success': True,
        'status': result.application.status,
        'workflow_status': result.application.workflow_status.code,
    })
