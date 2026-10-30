from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.demo import build_demo
from core.management.commands.seed_demo import Command
from core.models import TicketApplication
from core.views import PAGE_SIZE


class ViewsTests(TestCase):
    def setUp(self):
        self.demo = build_demo()
        self.apps = self.demo['applications']
        self.admin = get_user_model().objects.create_superuser('admin', 'a@example.com', 'admin')
        self.client.force_login(self.admin)

    def transition_url(self, key, code):
        app = self.apps[key]
        transition = self.demo['workflows']['kb'].transitions.get(code=code, from_status=app.workflow_status)
        return reverse('arm:application-transition', kwargs={'pk': app.pk, 'transition_id': transition.pk})

    def test_list_renders_buttons(self):
        response = self.client.get(reverse('arm:application-list'))
        self.assertContains(response, 'Одобрить')
        self.assertContains(response, 'btn-blocked')
        self.assertContains(response, 'Процесс не настроен')

    def test_form_page_and_submit(self):
        url = self.transition_url('kb_review', 'decline')
        response = self.client.get(url)
        self.assertContains(response, 'Причина отклонения')
        self.assertContains(response, 'js-visibility-rules')

        response = self.client.post(url, {'reason_choice': 'other', 'bas_declined_date': '2026-10-29'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Обязательное поле')

        response = self.client.post(url, {'reason_choice': 'other', 'reason_text': 'Передумал',
                                          'bas_declined_date': '2026-10-29'})
        self.assertRedirects(response, reverse('arm:application-detail', kwargs={'pk': self.apps['kb_review'].pk}))
        self.apps['kb_review'].refresh_from_db()
        self.assertEqual(self.apps['kb_review'].status, 'declined')

    def list_ids(self, **params):
        response = self.client.get(reverse('arm:application-list'), params)
        return sorted(row['application'].pk for row in response.context['rows'])

    def test_pagination(self):
        Command().bulk(PAGE_SIZE + 5)
        first = self.client.get(reverse('arm:application-list'))
        self.assertEqual(len(first.context['rows']), PAGE_SIZE)
        self.assertEqual(first.context['page'].paginator.count, TicketApplication.objects.count())
        self.assertContains(first, 'Страница 1 из')
        second = self.client.get(reverse('arm:application-list'), {'page': 2})
        self.assertEqual(len(second.context['rows']), 5 + len(self.apps))
        self.assertFalse(set(self.list_ids()) & set(self.list_ids(page=2)))
        self.assertEqual(len(self.list_ids(page=2, project=self.demo['projects']['bas'].pk)),
                         len(self.list_ids(project=self.demo['projects']['bas'].pk)))

    def test_list_filters(self):
        kb_course = self.apps['kb_review'].course
        flow = self.demo['flows']['open']
        user = self.apps['kb_docs_check'].user
        self.assertEqual(len(self.list_ids()), len(self.apps))
        self.assertEqual(self.list_ids(project=self.demo['projects']['bas'].pk),
                         sorted([self.apps['bas_review'].pk, self.apps['bas_accepted'].pk]))
        self.assertEqual(len(self.list_ids(course=kb_course.pk)), 6)
        self.assertEqual(self.list_ids(user=user.pk), [self.apps['kb_docs_check'].pk])
        self.assertEqual(len(self.list_ids(course=kb_course.pk, flow=flow.pk)), 4)
        self.assertEqual(self.list_ids(project=self.demo['projects']['bas'].pk, flow=flow.pk), [])
        self.assertEqual(len(self.list_ids(course='abc')), len(self.apps))

    def test_selected_filter_rendered_with_label(self):
        flow = self.demo['flows']['open']
        response = self.client.get(reverse('arm:application-list'), {'flow': flow.pk})
        self.assertContains(response, '<option value="{}" selected>Поток 1 — Python для школьников</option>'.format(
            flow.pk), html=True)
        self.assertContains(response, 'Сбросить')

    def test_filter_options(self):
        def options(kind, **params):
            response = self.client.get(reverse('arm:filter-options', kwargs={'kind': kind}), params)
            return [item['text'] for item in response.json()['results']]

        self.assertEqual(len(options('project')), 3)
        self.assertEqual(options('course', q='БПЛА'), ['Оператор БПЛА — БАС (демо, отчисления запрещены)'])
        self.assertEqual(options('course', project=self.demo['projects']['legacy'].pk),
                         ['Аналитик данных — Цифровые профессии (без процесса)'])
        self.assertIn('Глеб Кузнецов (student4)', options('user', q='student4'))
        self.assertEqual(options('user', q='гле'), ['Глеб Кузнецов (student4)'])
        self.assertEqual(options('course', q='бпла'), ['Оператор БПЛА — БАС (демо, отчисления запрещены)'])
        self.assertEqual(len(options('user', project=self.demo['projects']['bas'].pk)), 2)
        self.assertEqual(len(options('flow', course=self.apps['kb_review'].course.pk)), 3)
        self.assertEqual(options('flow', project=self.demo['projects']['legacy'].pk), [])
        response = self.client.get(reverse('arm:filter-options', kwargs={'kind': 'secret'}))
        self.assertEqual(response.status_code, 404)

    def test_list_buttons_open_modal(self):
        response = self.client.get(reverse('arm:application-list'))
        self.assertContains(response, 'data-transition-modal')
        self.assertContains(response, 'id="transition-modal"')

    def test_modal_flow(self):
        ajax = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}
        url = self.transition_url('kb_review', 'decline')

        response = self.client.get(url + '?next=/arm/', **ajax)
        self.assertNotContains(response, '<html')
        self.assertContains(response, 'class="modal-header"')
        self.assertContains(response, 'data-bs-dismiss="modal"')
        self.assertContains(response, 'class="form-select"')

        response = self.client.post(url, {'reason_choice': 'other', 'bas_declined_date': '2026-10-29',
                                          'next': '/arm/'}, **ajax)
        data = response.json()
        self.assertFalse(data['ok'])
        self.assertIn('is-invalid', data['html'])
        self.assertIn('Проверьте данные формы', data['html'])

        response = self.client.post(url, {'reason_choice': 'other', 'reason_text': 'Передумал',
                                          'bas_declined_date': '2026-10-29', 'next': '/arm/'}, **ajax)
        self.assertEqual(response.json(), {'ok': True, 'redirect': '/arm/'})

    def test_modal_guard_error_and_unsafe_next(self):
        ajax = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}
        url = self.transition_url('kb_approved_booked', 'docs_received')
        data = self.client.post(url, {'next': 'https://evil.example/'}, **ajax).json()
        self.assertFalse(data['ok'])
        self.assertIn('трансферт', data['error'])

        url = self.transition_url('kb_approved_active', 'docs_received')
        data = self.client.post(url, {'next': 'https://evil.example/'}, **ajax).json()
        self.assertEqual(data['redirect'], reverse('arm:application-detail',
                                                   kwargs={'pk': self.apps['kb_approved_active'].pk}))

    def test_admin_pages(self):
        workflow = self.demo['workflows']['kb']
        for url in (reverse('admin:workflow_workflow_changelist'),
                    reverse('admin:workflow_workflow_change', args=[workflow.pk]),
                    reverse('admin:workflow_transitionform_change', args=[self.demo['forms']['accept'].pk]),
                    reverse('admin:workflow_workflow_import')):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        response = self.client.get(reverse('admin:workflow_workflow_change', args=[workflow.pk]))
        self.assertContains(response, 'flowchart LR')


class ApiTests(TestCase):
    def setUp(self):
        self.demo = build_demo()
        self.apps = self.demo['applications']
        self.headers = {'HTTP_X_API_KEY': 'demo-key'}

    def test_docs(self):
        schema = self.client.get(reverse('api:schema'))
        self.assertEqual(schema.status_code, 200)
        body = schema.json()
        self.assertEqual(body['openapi'], '3.0.3')
        self.assertIn('/api/applications/{pk}/transitions/', body['paths'])
        self.assertIn('/api/applications/{pk}/status/', body['paths'])
        self.assertEqual(body['components']['securitySchemes']['ApiKey']['name'], 'X-Api-Key')

        docs = self.client.get(reverse('api:docs'))
        self.assertEqual(docs.status_code, 200)
        self.assertContains(docs, 'swagger-ui')
        self.assertContains(docs, reverse('api:schema'))

    def test_unauthorized(self):
        url = reverse('api:change-status', kwargs={'pk': self.apps['kb_docs_check'].pk})
        self.assertEqual(self.client.post(url, {'status': 'accepted'}).status_code, 401)

    def test_schema(self):
        url = reverse('api:transitions', kwargs={'pk': self.apps['kb_docs_check'].pk})
        data = self.client.get(url, **self.headers).json()
        accept = next(t for t in data['transitions'] if t['code'] == 'accept')
        names = [f['name'] for f in accept['form']]
        self.assertEqual(names[:3], ['accept_date', 'admission_order_date', 'admission_order_number'])
        employer = next(f for f in accept['form'] if f['name'] == 'employer_name')
        self.assertEqual(employer['visible_if'], {'param': 'employer_pays', 'value': 'true'})

    def test_change_status_by_base_status(self):
        url = reverse('api:change-status', kwargs={'pk': self.apps['kb_docs_check'].pk})
        response = self.client.post(url, {'status': 'accepted'}, **self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertIn('accept_date', response.json()['fields'])

        response = self.client.post(url, {'status': 'accepted', 'accept_date': '2026-11-01',
                                          'admission_order_date': '2026-10-30', 'admission_order_number': '3'},
                                    **self.headers)
        self.assertEqual(response.json(), {'success': True, 'status': 'accepted', 'workflow_status': 'accepted'})
