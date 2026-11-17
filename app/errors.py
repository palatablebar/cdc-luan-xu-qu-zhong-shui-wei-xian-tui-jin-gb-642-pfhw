"""领域异常 → HTTP 映射；业务代码只抛这些，不直接构造 HTTPException。"""
from __future__ import annotations


class CdcError(Exception):
    status_code = 400
    code = "E_UNKNOWN"

    def __init__(self, message: str, *, code: str | None = None,
                 detail: dict | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}
        if code:
            self.code = code

    def payload(self) -> dict:
        return {"error": self.code, "message": self.message, "detail": self.detail}


class BadRequest(CdcError):
    status_code = 400
    code = "E_BAD_REQUEST"


class NotFound(CdcError):
    status_code = 404
    code = "E_NOT_FOUND"


class Conflict(CdcError):
    status_code = 409
    code = "E_CONFLICT"


class StaleBatch(Conflict):
    code = "C_STALE_BATCH"


def install_handlers(app) -> None:
    from fastapi import Request
    from fastapi.responses import JSONResponse

    @app.exception_handler(CdcError)
    async def _cdc(request: Request, exc: CdcError):  # noqa: ANN001
        return JSONResponse(status_code=exc.status_code, content=exc.payload())

    @app.exception_handler(ValueError)
    async def _value(request: Request, exc: ValueError):  # noqa: ANN001
        return JSONResponse(status_code=400,
                            content={"error": "E_BAD_REQUEST", "message": str(exc),
                                     "detail": {}})
