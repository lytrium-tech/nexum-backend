from app.core.errors import NexumError


class GoalDuplicateError(NexumError):
    def __init__(self, message: str = "Ya existe una meta con este nombre."):
        super().__init__(status_code=409, error_code="goal_duplicate", message=message)


class GoalForbiddenError(NexumError):
    def __init__(self, message: str = "No tienes permiso para modificar esta meta."):
        super().__init__(status_code=403, error_code="goal_forbidden", message=message)


class GoalCompletedError(NexumError):
    def __init__(self, message: str = "La meta ya está completada, no admite más aportes."):
        super().__init__(status_code=409, error_code="goal_completed", message=message)


class GoalTargetAmountError(NexumError):
    def __init__(self, message: str = "El nuevo objetivo no puede ser menor al monto actual."):
        super().__init__(status_code=400, error_code="goal_target_invalid", message=message)


class GoalAmountExceededError(NexumError):
    def __init__(self, message: str = "El aporte excede el monto restante de la meta."):
        super().__init__(status_code=409, error_code="goal_amount_exceeded", message=message)


class GoalNotActiveError(NexumError):
    def __init__(self, message: str = "La meta no está activa."):
        super().__init__(status_code=400, error_code="goal_not_active", message=message)
