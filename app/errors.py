"""One error shape for every failure: {"error": {"code", "message", "details"}}."""

from typing import Any


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


def account_not_found(account_id: object) -> ApiError:
    return ApiError(404, "ACCOUNT_NOT_FOUND", f"Account {account_id} does not exist.")
