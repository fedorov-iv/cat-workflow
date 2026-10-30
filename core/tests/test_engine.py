import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from core.demo import build_demo
from core.models import CourseUserStatus, Transfert
from workflow import engine
from workflow.models import CHANNEL_API, CHANNEL_ARM, CHANNEL_SYSTEM, TransitionLog


class EngineTestCase(TestCase):
    def setUp(self):
        self.demo = build_demo()
        self.apps = self.demo['applications']
        self.s = self.demo['statuses']['kb']
        self.actor = get_user_model().objects.create_user('manager', is_staff=True)

    def codes(self, application, channel=CHANNEL_ARM, include_blocked=False):
        return [o.transition.code for o in
                engine.available_transitions(application, self.actor, channel, include_blocked=include_blocked)]

    def run_transition(self, key, code, data=None, files=None, channel=CHANNEL_ARM):
        return engine.execute_by_code(self.apps[key], code, self.actor, channel, data or {}, files)


class AvailableTransitionsTests(EngineTestCase):
    def test_buttons_follow_matrix(self):
        self.assertEqual(self.codes(self.apps['kb_review']), ['approve', 'decline'])

    def test_guard_hides_transition_but_reports_reason(self):
        app = self.apps['kb_approved_booked']
        self.assertNotIn('docs_received', self.codes(app))
        blocked = {o.transition.code: o for o in engine.available_transitions(app, include_blocked=True)}
        self.assertFalse(blocked['docs_received'].allowed)
        self.assertIn('трансферт', blocked['docs_received'].reasons[0])

    def test_channels_filter_transitions(self):
        app = self.apps['kb_approved_active']
        self.assertIn('change_flow', self.codes(app, CHANNEL_ARM))
        self.assertNotIn('change_flow', self.codes(app, CHANNEL_API))

    def test_project_without_workflow_has_no_transitions(self):
        self.assertEqual(self.codes(self.apps['legacy_review']), [])

    def test_project_guard_blocks_expel(self):
        self.assertNotIn('expel', self.codes(self.apps['bas_accepted']))

    @override_settings(SHOW_EXPEL_AND_FINISH_BUTTONS=False)
    def test_setting_flag_guard(self):
        self.assertNotIn('expel', self.codes(self.apps['kb_accepted']))


class ExecuteTests(EngineTestCase):
    def test_approve_writes_bound_fields_and_creates_transfert(self):
        flow = self.demo['flows']['open']
        result = self.run_transition('kb_review', 'approve', {'flow': flow.pk, 'enter_exam_date': '2026-11-10'})
        app = result.application
        app.refresh_from_db()
        self.assertEqual(app.workflow_status, self.s['approved'])
        self.assertEqual(app.status, 'approved')
        self.assertTrue(app._status.startswith('approved_'))
        self.assertEqual(app.flow, flow)
        self.assertEqual(str(app.transfert.enter_exam_date), '2026-11-10')
        self.assertEqual(app.reviewed_by, self.actor)
        log = TransitionLog.objects.get(application=app)
        self.assertEqual(log.written_bindings, ['application.flow', 'transfert.enter_exam_date'])

    def test_invalid_form_rolls_back(self):
        with self.assertRaises(engine.TransitionError) as ctx:
            self.run_transition('kb_review', 'approve', {'enter_exam_date': '2026-11-10'})
        self.assertEqual(ctx.exception.code, 'invalid_form')
        self.assertIn('flow', ctx.exception.form.errors)
        app = self.apps['kb_review']
        app.refresh_from_db()
        self.assertEqual(app.status, 'review')
        self.assertIsNone(app.transfert)

    def test_conditional_field_required_only_when_visible(self):
        data = {'reason_choice': 'other', 'bas_declined_date': '2026-10-29'}
        with self.assertRaises(engine.TransitionError) as ctx:
            self.run_transition('kb_review', 'decline', data)
        self.assertIn('reason_text', ctx.exception.form.errors)

        data['reason_choice'] = 'no_places'
        data['reason_text'] = 'должно быть проигнорировано'
        app = self.run_transition('kb_review', 'decline', data).application
        app.refresh_from_db()
        self.assertEqual(app.reason_choice, 'no_places')
        self.assertEqual(app.reason_text, '')

    def test_choice_set_restricts_values(self):
        with self.assertRaises(engine.TransitionError) as ctx:
            self.run_transition('kb_review', 'decline', {'reason_choice': 'unknown', 'bas_declined_date': '2026-10-29'})
        self.assertIn('reason_choice', ctx.exception.form.errors)

    def test_guard_failure_raises(self):
        with self.assertRaises(engine.TransitionError) as ctx:
            self.run_transition('kb_approved_booked', 'docs_received')
        self.assertEqual(ctx.exception.code, 'guard_failed')

    def test_wrong_status(self):
        transition = self.demo['workflows']['kb'].transitions.get(code='accept')
        with self.assertRaises(engine.TransitionError) as ctx:
            engine.execute(self.apps['kb_review'], transition, self.actor)
        self.assertEqual(ctx.exception.code, 'wrong_status')

    def test_custom_status_within_same_base(self):
        app = self.run_transition('kb_approved_active', 'docs_received').application
        self.assertEqual(app.workflow_status.code, 'docs_check')
        self.assertEqual(app.status, 'approved')

    def test_accept_runs_effects(self):
        data = {'accept_date': '2026-11-01', 'admission_order_date': '2026-10-30', 'admission_order_number': '17-к',
                'employer_pays': 'on', 'employer_name': 'ООО Ромашка', 'employer_ogrn': '1027700132195'}
        app = self.run_transition('kb_docs_check', 'accept', data).application
        app.transfert.refresh_from_db()
        self.assertEqual(app.status, 'accepted')
        self.assertTrue(app.transfert.employer_pays)
        self.assertEqual(app.transfert.employer_name, 'ООО Ромашка')
        self.assertTrue(CourseUserStatus.objects.filter(user=app.user, course=app.course, is_active=True).exists())

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_expel_with_document(self):
        data = {'reason_choice': 'legitimate', 'expulsion_date': '2026-10-29', 'expulsion_order_date': '2026-10-29',
                'expulsion_order_number': '5-о'}
        with self.assertRaises(engine.TransitionError) as ctx:
            self.run_transition('kb_accepted', 'expel', data)
        self.assertIn('reason_file', ctx.exception.form.errors)

        files = {'reason_file': SimpleUploadedFile('spravka.pdf', b'%PDF-1.4')}
        app = self.run_transition('kb_accepted', 'expel', data, files).application
        self.assertEqual(app.status, 'expelled')
        self.assertIsNotNone(app.reason_document)
        self.assertEqual(app.transfert.expulsion_order_number, '5-о')

    def test_finish_requires_closed_flow(self):
        self.assertNotIn('finish', self.codes(self.apps['kb_accepted']))
        app = self.run_transition('kb_accepted_closed_flow', 'finish', {'final_project_name': 'Бот'}).application
        self.assertEqual(app.status, 'finished')
        self.assertIsNotNone(app.dt_finished)
        self.assertNotIn('expel', self.codes(app, include_blocked=True))


class BaseStatusAdapterTests(EngineTestCase):
    def test_transition_to_base_from_api(self):
        app = engine.transition_to_base(
            self.apps['kb_docs_check'], 'accepted', None, CHANNEL_API,
            {'accept_date': '2026-11-01', 'admission_order_date': '2026-10-30', 'admission_order_number': '1'},
        ).application
        self.assertEqual(app.workflow_status.code, 'accepted')

    def test_transition_to_base_respects_matrix(self):
        with self.assertRaises(engine.TransitionError) as ctx:
            engine.transition_to_base(self.apps['kb_review'], 'accepted', None, CHANNEL_API, {})
        self.assertEqual(ctx.exception.code, 'no_transition')

    def test_system_channel_decline(self):
        app = engine.transition_to_base(self.apps['bas_review'], 'declined', None, CHANNEL_SYSTEM,
                                        {'reason_choice': 'by_recipient', 'bas_declined_date': '2026-10-29'}).application
        self.assertEqual(app.workflow_status.title, 'Отказ')

    def test_legacy_application_resolved_by_base_status(self):
        app = self.apps['kb_approved_active']
        app.workflow_status = None
        app.save()
        self.assertIn('docs_received', self.codes(app))


class ContractTests(EngineTestCase):
    def test_runtime_contract_for_expelled(self):
        form_field = self.demo['forms']['expel'].field_configs.get(binding='application.reason_choice')
        form_field.required = False
        form_field.save()
        data = {'expulsion_date': '2026-10-29', 'expulsion_order_date': '2026-10-29', 'expulsion_order_number': '1'}
        with self.assertRaises(engine.TransitionError) as ctx:
            self.run_transition('kb_accepted', 'expel', data)
        self.assertEqual(ctx.exception.code, 'contract_violation')
        self.apps['kb_accepted'].refresh_from_db()
        self.assertEqual(self.apps['kb_accepted'].status, 'accepted')
        self.assertEqual(self.apps['kb_accepted'].transfert.status, Transfert.STATUS_ACTIVATED)
