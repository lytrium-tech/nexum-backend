from app.core.errors import ConflictError, ForbiddenError


class AccountDuplicateError(ConflictError):
    def __init__(self, message: str = "Ya existe una cuenta activa con este nombre."):
        super().__init__(message=message)


class AccountForbiddenError(ForbiddenError):
    def __init__(self, message: str = "No tienes permiso para acceder a esta cuenta."):
        super().__init__(message=message)
