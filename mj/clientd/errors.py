"""clientd 结构化错误与 JSON 错误体。

每类错误带稳定 code 与 HTTP status,供前端呈现可读消息而非堆栈。
"""


class ClientdError(Exception):
    code = "INTERNAL"
    status = 500

    def __init__(self, message, *, code=None, status=None):
        super().__init__(message)
        if code is not None:
            self.code = code
        if status is not None:
            self.status = status


class ValidationError(ClientdError):
    code = "VALIDATION"
    status = 400


class NotFoundError(ClientdError):
    code = "NOT_FOUND"
    status = 404


class ConflictError(ClientdError):
    code = "CONFLICT"
    status = 409


def error_body(exc):
    return {"error": getattr(exc, "code", "INTERNAL"),
            "message": str(exc)}