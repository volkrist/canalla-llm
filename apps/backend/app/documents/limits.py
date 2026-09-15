from fastapi import HTTPException
from fastapi.responses import JSONResponse

from ..config import get_settings


class UploadLimit:
    """Bound multipart bytes before Starlette can spool an unbounded upload."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != "/documents" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        limit = get_settings().document_max_bytes + 1024 * 1024
        size = 0
        headers = dict(scope.get("headers", []))
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = limit + 1
        if declared > limit:
            return await JSONResponse({"detail": "Превышен лимит загрузки файла"}, status_code=413)(
                scope, receive, send
            )

        async def bounded_receive():
            nonlocal size
            message = await receive()
            size += len(message.get("body", b""))
            if size > limit:
                raise HTTPException(413, "Превышен лимит загрузки файла")
            return message

        return await self.app(scope, bounded_receive, send)
