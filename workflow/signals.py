from django.dispatch import Signal

# Отправляется после коммита транзакции перехода. kwargs: application, transition, log.
transition_executed = Signal()
