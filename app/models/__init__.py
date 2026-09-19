"""This project's own tables. Importing the package registers every model on
`Base.metadata`, which is what Alembic autogenerate and the tests rely on."""

from app.models.app_event import AppEvent
from app.models.audit_log import AuditLog
from app.models.bot_notification import BotNotification
from app.models.ingest import (
    BankCardPrefix,
    BankSmsPattern,
    Device,
    DeviceCredential,
    FinancialAccount,
    FinancialAccountIdentifier,
    PaymentCard,
    SmsEvent,
    TransactionCandidate,
)
from app.models.operator import Operator, OperatorSession
from app.models.setting import Setting

__all__ = [
    "AppEvent", "AuditLog", "BankCardPrefix", "BankSmsPattern", "BotNotification", "Device", "DeviceCredential",
    "FinancialAccount", "FinancialAccountIdentifier", "Operator", "OperatorSession", "PaymentCard", "Setting",
    "SmsEvent", "TransactionCandidate",
]
