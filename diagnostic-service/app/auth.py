"""
auth.py — OAuth 2.0 bearer-token validation for the API (REQ-046).

Off by default (``OAUTH2_ENABLED=false``) so local/compose runs need no
identity provider. When enabled, every non-ops route requires
``Authorization: Bearer <JWT>``; the token's signature is verified against
the provider's JWKS (``OAUTH2_JWKS_URL``, RS256/ES256), and ``iss`` / ``aud``
/ ``exp`` are checked. Works with any OIDC provider — e.g. Amazon Cognito:
``https://cognito-idp.<region>.amazonaws.com/<pool-id>/.well-known/jwks.json``.

Mirrors ingestion-service's ``SecurityConfig.java`` (Spring resource server).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)

ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384"]


@lru_cache(maxsize=4)
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=300)


def _signing_key(token: str, jwks_url: str) -> Any:
    return _jwks_client(jwks_url).get_signing_key_from_jwt(token).key


def require_token(
    request: Request, token: Annotated[str | None, Depends(oauth2_scheme)]
) -> dict[str, Any] | None:
    """FastAPI dependency: returns the validated claims, or None when auth is disabled."""
    settings = request.app.state.settings
    if not settings.oauth2_enabled:
        return None
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "invalid or missing bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise unauthorized
    try:
        key = _signing_key(token, settings.oauth2_jwks_url)
        claims: dict[str, Any] = jwt.decode(
            token,
            key,
            algorithms=ALGORITHMS,
            audience=settings.oauth2_audience or None,
            issuer=settings.oauth2_issuer or None,
            options={"require": ["exp"], "verify_aud": bool(settings.oauth2_audience)},
            leeway=30,
        )
    except (jwt.PyJWTError, jwt.PyJWKClientError) as exc:
        raise unauthorized from exc
    return claims


Claims = Annotated[dict[str, Any] | None, Depends(require_token)]


def subject(claims: dict[str, Any] | None) -> str | None:
    """Who made the request — for audit logs (REQ-053)."""
    if not claims:
        return None
    return str(claims.get("sub") or claims.get("client_id") or "") or None
