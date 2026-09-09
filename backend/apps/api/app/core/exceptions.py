"""业务异常体系：定义可被全局异常处理器统一响应的领域异常。"""

from typing import Any


class AppError(Exception):
    """业务异常基类。"""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class AuthError(AppError):
    """鉴权失败（401）。"""

    status_code = 401
    code = "auth_error"


class ForbiddenError(AppError):
    """权限不足（403）。"""

    status_code = 403
    code = "forbidden"


class NotFoundError(AppError):
    """资源不存在（404）。"""

    status_code = 404
    code = "not_found"


class ValidationError(AppError):
    """业务校验失败（422）。"""

    status_code = 422
    code = "validation_error"


class ConflictError(AppError):
    """资源冲突（409）。"""

    status_code = 409
    code = "conflict"


class QuotaExceededError(AppError):
    """成本/配额超限（429）。"""

    status_code = 429
    code = "quota_exceeded"


class ProviderUnavailableError(AppError):
    """LLM Provider 不可用（503）。"""

    status_code = 503
    code = "provider_unavailable"


class ExternalServiceError(AppError):
    """外部系统调用失败（502）。"""

    status_code = 502
    code = "external_service_error"