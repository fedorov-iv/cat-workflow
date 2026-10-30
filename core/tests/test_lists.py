from django.contrib.auth import get_user_model
from django.test import TestCase

from core.demo import build_demo
from core.models import Course, LearningFlow, Project, TicketApplication
from core.workflow_hooks import application_target
from workflow import engine
from workflow.bindings import ModelFieldBinding
from workflow.forms import build_form, form_schema
from workflow.models import CHANNEL_ARM, ChoiceSet, TransitionForm, TransitionFormField
from workflow.registry import bindings, register_binding
from workflow.serialization import export_workflow, import_workflow
from workflow.validation import ERROR, WARNING, validate_form

if 'test.course' not in bindings:
    register_binding(ModelFieldBinding(
        key='test.course', label='Заявка: Курс', model=TicketApplication, field_name='course',
        target=application_target, queryset=lambda application: Course.objects.filter(project=application.project),
    ))

EXPEL_DATA = {'reason_choice': 'legitimate', 'expulsion_date': '2026-10-29', 'expulsion_order_date': '2026-10-29',
              'expulsion_order_number': '7-О'}


class ListsTestCase(TestCase):
    def setUp(self):
        self.demo = build_demo()
        self.apps = self.demo['applications']
        self.actor = get_user_model().objects.create_user('manager', is_staff=True)

    def transition(self, key, code):
        app = self.apps[key]
        return app.workflow_status.outgoing.get(code=code)

    def expel_form(self, data):
        app = self.apps['kb_accepted']
        return build_form(self.transition('kb_accepted', 'expel'), app, data={**EXPEL_DATA, **data})


class DependentChoiceSetTests(ListsTestCase):
    def test_child_value_must_match_parent(self):
        form = self.expel_form({'reason_detail': 'absence'})
        self.assertFalse(form.is_valid())
        self.assertIn('Причина отчисления', form.errors['reason_detail'][0])

    def test_matching_and_unrestricted_values(self):
        self.assertTrue(self.expel_form({'reason_choice': 'not_legitimate', 'reason_detail': 'absence'}).is_valid())
        self.assertTrue(self.expel_form({'reason_choice': 'not_legitimate', 'reason_detail': 'relocation'}).is_valid())
        self.assertTrue(self.expel_form({'reason_choice': 'other', 'reason_text': 'x', 'reason_detail': ''}).is_valid())

    def test_value_is_saved(self):
        result = engine.execute_by_code(self.apps['kb_accepted'], 'expel', self.actor, CHANNEL_ARM,
                                        {**EXPEL_DATA, 'reason_choice': 'not_legitimate', 'reason_detail': 'debts'})
        result.application.refresh_from_db()
        self.assertEqual(result.application.reason_detail, 'debts')

    def test_widget_markup_and_schema(self):
        form = self.expel_form({})
        html = str(form['reason_detail'])
        self.assertIn('data-depends-on="reason_choice"', html)
        self.assertIn('data-autocomplete="local"', html)
        self.assertIn('data-parent="[&quot;legitimate&quot;]"', html)
        self.assertNotIn('data-autocomplete', str(form['reason_choice']))
        schema = {item['name']: item for item in form_schema(form)}
        self.assertEqual(schema['reason_detail']['depends_on'], 'reason_choice')
        self.assertEqual(schema['reason_detail']['widget'], 'autocomplete')
        self.assertEqual(schema['reason_choice']['widget'], 'select')
        relocation = next(c for c in schema['reason_detail']['choices'] if c['value'] == 'relocation')
        self.assertEqual(relocation['parents'], ['legitimate', 'not_legitimate'])


class DependentModelListTests(ListsTestCase):
    def setUp(self):
        super().setUp()
        form_def = TransitionForm.objects.create(code='move', title='Перевод')
        course_field = TransitionFormField.objects.create(form=form_def, binding='test.course', order=0)
        TransitionFormField.objects.create(form=form_def, binding='application.flow', order=1,
                                           depends_on=course_field)
        self.form_def = form_def
        transition = self.transition('kb_approved_active', 'change_flow')
        transition.form = form_def
        self.transition_obj = transition
        self.app = self.apps['kb_approved_active']
        self.other_course = Course.objects.create(title='Другой курс', project=self.app.project)

    def test_flow_options_linked_to_course_by_fk(self):
        flow = self.demo['flows']['open']
        ok = build_form(self.transition_obj, self.app, data={'course': self.app.course_id, 'flow': flow.pk})
        self.assertTrue(ok.is_valid(), ok.errors)
        bad = build_form(self.transition_obj, self.app, data={'course': self.other_course.pk, 'flow': flow.pk})
        self.assertFalse(bad.is_valid())
        self.assertIn('flow', bad.errors)
        self.assertIn('data-parent="[&quot;{}&quot;]"'.format(self.app.course_id), str(ok['flow']))

    def test_config_is_clean(self):
        self.assertEqual(validate_form(self.form_def), [])


class ValidationTests(ListsTestCase):
    def issues(self, form_def, level):
        return [i.message for i in validate_form(form_def) if i.level == level]

    def test_demo_forms_are_clean(self):
        for form_def in self.demo['forms'].values():
            self.assertEqual(validate_form(form_def), [], form_def.code)

    def test_autocomplete_on_non_list(self):
        field = self.demo['forms']['finish'].field_configs.get()
        field.widget = TransitionFormField.WIDGET_AUTOCOMPLETE
        field.save()
        self.assertTrue(any('не является списком' in m for m in self.issues(field.form, WARNING)))

    def test_bad_dependencies(self):
        form_def = self.demo['forms']['expel']
        reason = form_def.field_configs.get(binding='application.reason_choice')
        text = form_def.field_configs.get(binding='application.reason_text')
        text.depends_on = reason
        text.save()
        flow = TransitionFormField.objects.create(form=form_def, binding='application.flow', depends_on=reason)
        reason.depends_on = flow
        reason.save()
        errors = self.issues(form_def, ERROR)
        self.assertTrue(any('«Опишите причину' in m and 'не является списком' in m for m in errors))
        self.assertTrue(any('может зависеть только от списка из модели' in m for m in errors))
        self.assertTrue(any('цикл' in m for m in errors))

    def test_choice_set_without_parent_values(self):
        ChoiceSet.objects.get(code='expel_details').items.update(parent_values='')
        self.assertTrue(any('не будет сужаться' in m for m in self.issues(self.demo['forms']['expel'], WARNING)))


class SerializationTests(ListsTestCase):
    def test_import_restores_dependencies(self):
        data = export_workflow(self.demo['workflows']['kb'])
        data['forms'] = {code + '_copy': form for code, form in data['forms'].items()}
        for t in data['transitions']:
            if t['form']:
                t['form'] += '_copy'
        ChoiceSet.objects.filter(code='expel_details').update(code='expel_details_old')

        workflow = import_workflow(data, Project.objects.create(title='Новый'))

        form_def = TransitionForm.objects.get(code='expel_copy')
        detail = form_def.field_configs.get(binding='application.reason_detail')
        self.assertEqual(detail.depends_on.binding, 'application.reason_choice')
        self.assertEqual(detail.depends_on.form, form_def)
        self.assertEqual(detail.widget, TransitionFormField.WIDGET_AUTOCOMPLETE)
        self.assertEqual(detail.choice_set.items.get(value='relocation').parent_values, 'legitimate,not_legitimate')
        self.assertEqual(workflow.transitions.get(code='expel').form, form_def)
