from fastapi import HTTPException, status


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
