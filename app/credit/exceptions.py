from app.core.errors import NexumError


class CreditDomainError(NexumError):
    """Base exception for all credit domain errors."""

    status_code: int = 400
    error_code: str = "credit_domain_error"
    message: str = "Error en el dominio de crédito."

    def __init__(
        self,
        message: str | None = None,
        error_code: str | None = None,
        status_code: int | None = None,
        detail: dict | None = None,
    ) -> None:
        super().__init__(
            message=message or self.__class__.message,
            error_code=error_code or self.__class__.error_code,
            status_code=status_code or self.__class__.status_code,
            detail=detail or {},
        )


class CreditCardNotFoundError(CreditDomainError):
    status_code = 404
    error_code = "credit_card_not_found"
    message = "Tarjeta de crédito no encontrada."

    def __init__(self, message: str = "Credit card not found", detail: dict | None = None) -> None:
        super().__init__(message=message, detail=detail)


class CreditCardInactiveError(CreditDomainError):
    status_code = 400
    error_code = "credit_card_inactive"
    message = "La tarjeta de crédito está inactiva."

    def __init__(
        self, message: str = "Credit card is inactive", detail: dict | None = None
    ) -> None:
        super().__init__(message=message, detail=detail)


class CreditLimitExceededError(CreditDomainError):
    status_code = 409
    error_code = "credit_limit_exceeded"
    message = "Límite de crédito excedido."

    def __init__(self, message: str = "Credit limit exceeded", detail: dict | None = None) -> None:
        super().__init__(message=message, detail=detail)


class InvalidPaymentAmountError(CreditDomainError):
    status_code = 400
    error_code = "credit_payment_amount_invalid"
    message = "El monto de pago no es válido."

    def __init__(
        self,
        message: str = "Payment amount exceeds current debt",
        detail: dict | None = None,
    ) -> None:
        super().__init__(message=message, detail=detail)


class CreditCardForbiddenError(CreditDomainError):
    status_code = 403
    error_code = "credit_card_forbidden"
    message = "No tienes permiso para acceder a esta tarjeta de crédito."

    def __init__(
        self,
        message: str = "Forbidden access to credit card",
        detail: dict | None = None,
    ) -> None:
        super().__init__(message=message, detail=detail)


class CreditIdempotencyConflictError(CreditDomainError):
    status_code = 409
    error_code = "credit_idempotency_conflict"
    message = "Conflicto de idempotencia en operación de crédito."

    def __init__(
        self,
        message: str = "Idempotency key collision with conflicting parameters",
        detail: dict | None = None,
    ) -> None:
        super().__init__(message=message, detail=detail)


class CreditAllocationError(CreditDomainError):
    status_code = 409
    error_code = "credit_allocation_error"
    message = "Error en la asignación de pagos o ajustes de crédito."

    def __init__(
        self,
        message: str = "Invalid credit payment or adjustment allocation",
        detail: dict | None = None,
    ) -> None:
        super().__init__(message=message, detail=detail)


class CreditBillingCycleClosedError(CreditDomainError):
    status_code = 409
    error_code = "credit_billing_cycle_closed"
    message = "El ciclo de facturación se encuentra cerrado o liquidado."

    def __init__(
        self,
        message: str = "Billing cycle is already closed or settled",
        detail: dict | None = None,
    ) -> None:
        super().__init__(message=message, detail=detail)


class CreditStatementImmutableError(CreditDomainError):
    status_code = 409
    error_code = "credit_statement_immutable"
    message = "El extracto de crédito es inmutable y no puede modificarse en su lugar."

    def __init__(
        self,
        message: str = "Closed credit card statement is immutable",
        detail: dict | None = None,
    ) -> None:
        super().__init__(message=message, detail=detail)


class CreditGoalReservationBreachError(CreditDomainError):
    status_code = 409
    error_code = "credit_goal_reservation_breach"
    message = "El pago excede el saldo disponible tras reservas de metas."

    def __init__(
        self,
        message: str = "Payment amount violates spendable balance and goal reservations",
        detail: dict | None = None,
    ) -> None:
        super().__init__(message=message, detail=detail)


class CreditPartitionError(CreditDomainError):
    status_code = 409
    error_code = "credit_partition_error"
    message = "Conflicto de partición de deuda canónica vs legacy."

    def __init__(
        self,
        message: str = "Invalid operation across canonical and legacy partitions",
        detail: dict | None = None,
    ) -> None:
        super().__init__(message=message, detail=detail)


class CreditInstallmentCeilingError(CreditDomainError):
    status_code = 409
    error_code = "credit_installment_ceiling_error"
    message = "El pago excede el techo del installment (total_amount)."

    def __init__(
        self,
        message: str = "Payment exceeds installment ceiling (principal + assessed interest)",
        detail: dict | None = None,
    ) -> None:
        super().__init__(message=message, detail=detail)


class CreditLifecycleInvalidityError(CreditDomainError):
    status_code = 400
    error_code = "credit_lifecycle_invalidity"
    message = "Transición o estado de ciclo de vida de crédito no válido."

    def __init__(
        self,
        message: str = "Invalid credit lifecycle status or transition",
        detail: dict | None = None,
    ) -> None:
        super().__init__(message=message, detail=detail)


class CreditAmbiguousAmountError(CreditDomainError):
    status_code = 400
    error_code = "credit_ambiguous_amount"
    message = "Monto de compra ambiguo o en conflicto."

    def __init__(
        self,
        message: str = "Conflicting or ambiguous purchase amounts supplied",
        detail: dict | None = None,
    ) -> None:
        super().__init__(message=message, detail=detail)


class CreditActivationBoundaryError(CreditDomainError):
    status_code = 400
    error_code = "credit_activation_boundary_violation"
    message = "La fecha de la compra precede la activación canónica de la tarjeta."

    def __init__(
        self,
        message: str = "Purchase occurred_at predates canonical activation boundary",
        detail: dict | None = None,
    ) -> None:
        super().__init__(message=message, detail=detail)
