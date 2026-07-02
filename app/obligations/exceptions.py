from fastapi import HTTPException, status

from app.core.errors import ConflictError, ValidationError


class ObligationNotFoundError(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Obligation not found",
        )


class ObligationForbiddenError(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions to access this obligation",
        )


class ObligationInactiveError(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot pay an inactive obligation",
        )


class ObligationAmountMismatchError(HTTPException):
    def __init__(self, required_amount: str) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Payment amount must match the obligation quota: {required_amount}",
        )


class ObligationAlreadyPaidError(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail="This obligation has already been paid for this period",
        )


class ObligationOverpaymentError(HTTPException):
    def __init__(self, remaining: str) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Payment exceeds remaining amount for this period: {remaining}",
        )


class ObligationValidationError(ValidationError, ValueError):
    """Error de validación para Obligación."""

    error_code = "obligation_validation_error"


class ObligationDuplicateError(ConflictError, ValueError):
    """Ya existe una obligación activa con este nombre."""

    error_code = "obligation_duplicate"

class ObligationPeriodAmountRequiredError(ConflictError):
    error_code = "OBLIGATION_PERIOD_AMOUNT_REQUIRED"
    message = "Cannot pay a variable period until its amount is defined."

class ObligationPaymentExceedsBalanceError(ConflictError):
    error_code = "OBLIGATION_PAYMENT_EXCEEDS_REMAINING_BALANCE"
    
    def __init__(self, remaining: str = ""):
        message = f"Payment exceeds remaining balance. {remaining}" if remaining else "Payment exceeds remaining balance."
        super().__init__(message=message)
