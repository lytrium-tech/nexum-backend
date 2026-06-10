from app.core.errors import ConflictError


class InsufficientFundsError(ConflictError):
    error_code = "insufficient_funds"

    def __init__(self, message: str = "Saldo insuficiente en la cuenta para realizar el gasto."):
        super().__init__(message=message)
