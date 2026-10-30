from django.test import TestCase

from core.demo import build_demo
from core.models import Project
from workflow.models import TransitionGuard, WorkflowStatus, WorkflowTransition
from workflow.serialization import clone_workflow, export_workflow, import_workflow
from workflow.validation import ERROR, has_errors, validate_workflow


class ValidationTests(TestCase):
    def setUp(self):
        self.demo = build_demo()
        self.workflow = self.demo['workflows']['kb']
        self.s = self.demo['statuses']['kb']

    def messages(self, level=None):
        return [i.message for i in validate_workflow(self.workflow) if level is None or i.level == level]

    def test_demo_is_clean(self):
        self.assertEqual(self.messages(), [])

    def test_base_matrix_violation(self):
        WorkflowTransition.objects.create(workflow=self.workflow, code='bad', from_status=self.s['declined'],
                                          to_status=self.s['accepted'], button_label='Вернуть')
        self.assertTrue(any('базовая матрица' in m for m in self.messages(ERROR)))

    def test_expel_form_must_fill_reason(self):
        self.demo['forms']['expel'].field_configs.filter(binding='application.reason_choice').delete()
        self.assertTrue(any('reason_choice' in m for m in self.messages(ERROR)))

    def test_unknown_guard_and_bad_params(self):
        transition = self.workflow.transitions.get(code='approve')
        TransitionGuard.objects.create(transition=transition, code='nope')
        TransitionGuard.objects.create(transition=transition, code='transfert_status_in', params={'wrong': 1})
        errors = self.messages(ERROR)
        self.assertTrue(any('неизвестное условие nope' in m for m in errors))
        self.assertTrue(any('неизвестный параметр "wrong"' in m for m in errors))
        self.assertTrue(any('не задан обязательный параметр "statuses"' in m for m in errors))

    def test_unreachable_and_dead_end_statuses(self):
        WorkflowStatus.objects.create(workflow=self.workflow, code='limbo', title='Лимбо', base_status='admitted')
        warnings = self.messages()
        self.assertTrue(any('«Лимбо» недостижим' in m for m in warnings))
        self.assertTrue(any('Из статуса «Лимбо» нет переходов' in m for m in warnings))

    def test_ambiguous_api_transition(self):
        WorkflowTransition.objects.create(workflow=self.workflow, code='decline2', from_status=self.s['review'],
                                          to_status=self.s['declined'], button_label='Ещё отклонить',
                                          form=self.demo['forms']['decline'], channels=['api'])
        self.assertTrue(any('неоднозначным' in m for m in self.messages()))


class SerializationTests(TestCase):
    def setUp(self):
        self.demo = build_demo()

    def test_roundtrip_into_new_project(self):
        source = self.demo['workflows']['kb']
        project = Project.objects.create(title='Новый проект')
        clone = clone_workflow(source, project)
        self.assertFalse(clone.is_active)
        exported_source = export_workflow(source)
        exported_clone = export_workflow(clone)
        exported_source.pop('title'), exported_clone.pop('title')
        self.assertEqual(exported_source, exported_clone)
        self.assertFalse(has_errors(validate_workflow(clone)))
        self.assertEqual(clone.statuses.count(), source.statuses.count())

    def test_import_refuses_project_with_workflow(self):
        with self.assertRaises(ValueError):
            import_workflow(export_workflow(self.demo['workflows']['kb']), self.demo['projects']['bas'])
