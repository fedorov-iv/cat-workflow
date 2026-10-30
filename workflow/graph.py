from .base_matrix import base_status_title


def _label(text):
    return str(text).replace('"', '#quot;')


def to_mermaid(workflow):
    lines = ['flowchart LR']
    for status in workflow.statuses.all():
        shape = '(["{}"])' if status.is_final else '["{}"]'
        lines.append('    s{}{}'.format(status.id, shape.format(
            _label('{}<br/><small>{}</small>'.format(status.title, base_status_title(status.base_status))))))
    if workflow.initial_status_id:
        lines.append('    start(( )) --> s{}'.format(workflow.initial_status_id))
    for t in workflow.transitions.filter(is_active=True).select_related('form'):
        label = t.button_label + (' *' if t.form_id else '')
        lines.append('    s{} -->|"{}"| s{}'.format(t.from_status_id, _label(label), t.to_status_id))
    return '\n'.join(lines)
