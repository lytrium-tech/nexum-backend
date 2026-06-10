class CreditDomainError(Exception):
    pass


class CreditCardNotFoundError(CreditDomainError):
    def __init__(self, message="Credit card not found"):
        super().__init__(message)


class CreditCardInactiveError(CreditDomainError):
    def __init__(self, message="Credit card is inactive"):
        super().__init__(message)


class CreditLimitExceededError(CreditDomainError):
    def __init__(self, message="Credit limit exceeded"):
        super().__init__(message)


class InvalidPaymentAmountError(CreditDomainError):
    def __init__(self, message="Payment amount exceeds current debt"):
        super().__init__(message)
