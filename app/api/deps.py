from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.errors import AppError
from app.services.shop import ShopService

bearer_scheme = HTTPBearer(auto_error=False, description="Demo API key (shk_...) bound to a customer.")


def get_service(request: Request) -> ShopService:
    return request.app.state.service


Service = Annotated[ShopService, Depends(get_service)]


def current_customer(
    service: Service,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> uuid.UUID:
    """Customer identity comes ONLY from the API key, never from request arguments."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError("UNAUTHORIZED")
    return service.authenticate(credentials.credentials)


Customer = Annotated[uuid.UUID, Depends(current_customer)]
