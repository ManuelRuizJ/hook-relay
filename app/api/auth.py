"""Bearer API key authentication for the /api/v1 routes."""

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.deps import DbSession
from app.core.security import hash_api_key
from app.models.api_key import ApiKey
from app.repositories.api_key_repository import ApiKeyRepository

logger = logging.getLogger(__name__)

# auto_error=False: return a 401 with WWW-Authenticate ourselves instead of
# FastAPI's default 403 for a missing header. HTTPBearer also adds the
# "Authorize" button to /docs.
bearer_scheme = HTTPBearer(
    auto_error=False,
    description="API key created with `python -m app.cli create-api-key`.",
)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_api_key(
    request: Request,
    session: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> ApiKey:
    if credentials is None:
        raise _unauthorized("Missing API key: send 'Authorization: Bearer <key>'")

    # Looked up by hash: the plaintext is never compared or stored, and the
    # timing of an index lookup on a SHA-256 tells an attacker nothing useful.
    api_key = await ApiKeyRepository(session).get_active_by_hash(
        hash_api_key(credentials.credentials)
    )
    if api_key is None:
        logger.warning(
            "Rejected request with an invalid or revoked API key",
            extra={"path": request.url.path},
        )
        raise _unauthorized("Invalid or revoked API key")

    request.state.api_key = api_key
    return api_key


ApiKeyDep = Annotated[ApiKey, Depends(require_api_key)]
