import logging

from django.dispatch import receiver

from workflow.signals import transition_executed

logger = logging.getLogger(__name__)


@receiver(transition_executed)
def push_to_kafka(sender, application, transition, log, **kwargs):
    # Заглушка KafkaPusher().send_object_info(...): интеграции получают базовый статус и код проектного.
    logger.info('kafka: application=%s status=%s workflow_status=%s', application.pk, application.status,
                application.workflow_status.code)
