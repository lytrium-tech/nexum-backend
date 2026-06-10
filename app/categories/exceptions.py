from app.core.errors import ConflictError, ForbiddenError


class CategoryDuplicateError(ConflictError):
    def __init__(self, message: str = "Ya existe una categoría activa con este nombre."):
        super().__init__(message=message)


class CategoryForbiddenError(ForbiddenError):
    def __init__(
        self,
        message: str = "No puedes modificar ni eliminar categorías globales o de otro usuario.",
    ):
        super().__init__(message=message)
