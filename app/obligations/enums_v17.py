from enum import Enum

class ObligationType(str, Enum):
    recurring = "recurring"
    one_time = "one_time"

class Frequency(str, Enum):
    monthly = "monthly"
    weekly = "weekly"
    biweekly = "biweekly"
    yearly = "yearly"
    one_time = "one_time"

class AmountType(str, Enum):
    fixed = "fixed"
    variable = "variable"

class PeriodStatus(str, Enum):
    pending_amount_definition = "pending_amount_definition"
    pending_payment = "pending_payment"
    partially_paid = "partially_paid"
    paid = "paid"
    overdue = "overdue"
    skipped = "skipped"
    cancelled = "cancelled"

class ObligationStatus(str, Enum):
    active = "active"
    completed = "completed"
    archived = "archived"
    cancelled = "cancelled"

class PaymentApplicationStrategy(str, Enum):
    specific_period = "specific_period"
    fifo = "fifo"

class FXQuoteStatus(str, Enum):
    active = "active"
    expired = "expired"
    used = "used"
    rejected = "rejected"
