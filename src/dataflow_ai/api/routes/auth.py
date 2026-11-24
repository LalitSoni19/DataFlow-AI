"""Authentication endpoints (login, logout, password reset, account deletion)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from dataflow_ai.api.rate_limiter import rate_limit_dependency
from dataflow_ai.security import (
    RESET_TOKEN_STORE,
    TOKEN_BLACKLIST,
    create_jwt,
    hash_password,
    verify_jwt,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

AUTH_LOGIN_LIMIT = rate_limit_dependency("login", max_requests=5, window_seconds=60)
AUTH_RESET_LIMIT = rate_limit_dependency("password_reset", max_requests=3, window_seconds=3600)
AUTH_RESET_CONFIRM_LIMIT = rate_limit_dependency("password_reset_confirm", max_requests=5, window_seconds=60)


@dataclass(frozen=True)
class UserSession:
    user_id: str
    role: str
    jti: str
    exp: float


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=8, max_length=128)


class LoginResponse(BaseModel):
    status: str
    access_token: str
    token_type: str = "bearer"
    user_id: str
    role: str


class PasswordResetRequest(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ConfirmPasswordResetRequest(BaseModel):
    token: str = Field(min_length=20, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class AuthResponse(BaseModel):
    status: str
    message: str
    reset_token: str | None = None


_USER_REGISTRY: dict[str, dict[str, Any]] = {
    "admin": {
        "user_id": "admin-001",
        "email": "admin@dataflow.ai",
        "role": "admin",
        "password_hash": hash_password("AdminSecurePass123!"),
    },
    "analyst": {
        "user_id": "analyst-001",
        "email": "analyst@dataflow.ai",
        "role": "analyst",
        "password_hash": hash_password("AnalystPass123!"),
    },
    "C-0001": {
        "user_id": "C-0001",
        "email": "customer1@dataflow.ai",
        "role": "customer",
        "password_hash": hash_password("CustomerPass123!"),
    },
    "C-0002": {
        "user_id": "C-0002",
        "email": "customer2@dataflow.ai",
        "role": "customer",
        "password_hash": hash_password("CustomerPass123!"),
    },
}


@lru_cache(maxsize=4)
def _key_client(url: str):
    from jwt import PyJWKClient
    return PyJWKClient(url, timeout=5)


def _provider_session(token: str, settings) -> UserSession:
    import jwt

    if not (settings.oidc_issuer and settings.oidc_audience and
            (settings.oidc_jwks_url or "").startswith("https://")):
        raise HTTPException(503, "Configure the workspace identity provider.")
    try:
        key = _key_client(settings.oidc_jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=settings.oidc_audience,
                            issuer=settings.oidc_issuer, options={"require": ["exp", "sub", "iss", "aud"]})
    except jwt.PyJWTError as error:
        raise HTTPException(401, "Identity provider token verification failed.") from error
    role = claims.get("dataflow_role", "analyst")
    return UserSession(claims["sub"], role, claims.get("jti", ""), claims["exp"])


def get_current_user(request: Request) -> UserSession:
    """Extract and verify JWT bearer token from Authorization header."""
    auth_header = request.headers.get("Authorization")
    settings = getattr(request.app.state, "settings", None)
    is_production = getattr(settings, "is_production", False)
    auth_required = getattr(settings, "auth_required", False)
    jwt_secret = getattr(settings, "jwt_secret", "dataflow-development-secret-key-32-chars-min")

    if auth_header:
        parts = auth_header.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid Authorization header. Must use 'Bearer <token>'.")
        token = parts[1].strip()
        if is_production or auth_required:
            session = _provider_session(token, settings)
            request.state.workspace_settings = settings.workspace(settings.oidc_issuer, session.user_id)
            return session
        try:
            payload = verify_jwt(token, secret=jwt_secret)
            return UserSession(
                user_id=payload["sub"],
                role=payload.get("role", "user"),
                jti=payload.get("jti", ""),
                exp=float(payload.get("exp", 0)),
            )
        except ValueError as err:
            raise HTTPException(status_code=401, detail="Authentication failed: Invalid or expired token.") from err

    if is_production or auth_required:
        raise HTTPException(status_code=401, detail="Authentication credentials were not provided.")

    return UserSession(user_id="dev-admin", role="admin", jti="dev-session", exp=float("inf"))


def require_role(*allowed_roles: str):
    """Dependency checking user role against allowed roles."""
    def role_checker(current_user: UserSession = Depends(get_current_user)) -> UserSession:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"Forbidden: Action requires one of roles {list(allowed_roles)}; caller has role '{current_user.role}'.",
            )
        return current_user
    return role_checker


@router.post("/login", response_model=LoginResponse, dependencies=[Depends(AUTH_LOGIN_LIMIT)])
def login(request: Request, payload: LoginRequest) -> dict[str, Any]:
    """Authenticate with username/password and return a signed JWT."""
    user = _USER_REGISTRY.get(payload.username)
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    settings = getattr(request.app.state, "settings", None)
    secret = getattr(settings, "jwt_secret", "dataflow-development-secret-key-32-chars-min")
    token = create_jwt(user_id=user["user_id"], role=user["role"], secret=secret, expires_in_seconds=1800)

    return {
        "status": "authenticated",
        "access_token": token,
        "token_type": "bearer",
        "user_id": user["user_id"],
        "role": user["role"],
    }


@router.post("/logout")
def logout(current_user: UserSession = Depends(get_current_user)) -> dict[str, str]:
    """Revoke current token by recording JTI in blacklist."""
    if current_user.jti and current_user.exp != float("inf"):
        TOKEN_BLACKLIST.revoke(current_user.jti, current_user.exp)
    return {"status": "success", "message": "Successfully logged out. Token has been revoked."}


@router.post("/password-reset", response_model=AuthResponse, dependencies=[Depends(AUTH_RESET_LIMIT)])
def password_reset(request: Request, payload: PasswordResetRequest) -> dict[str, str | None]:
    """Request a password reset link for the given email."""
    RESET_TOKEN_STORE.generate_reset_token(payload.email)

    return {
        "status": "accepted",
        "message": "If that email is registered in our system, a password reset link has been dispatched.",
        "reset_token": None,
    }


@router.post("/password-reset/confirm", response_model=AuthResponse, dependencies=[Depends(AUTH_RESET_CONFIRM_LIMIT)])
def confirm_password_reset(payload: ConfirmPasswordResetRequest) -> dict[str, str]:
    """Verify single-use token within 15 minutes and update password."""
    try:
        email = RESET_TOKEN_STORE.verify_and_consume_token(payload.token)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err

    for user_data in _USER_REGISTRY.values():
        if user_data.get("email", "").lower() == email.lower():
            user_data["password_hash"] = hash_password(payload.new_password)
            break

    return {
        "status": "success",
        "message": f"Password has been successfully updated for {email}.",
    }


@router.delete("/account", response_model=AuthResponse)
def delete_own_account(current_user: UserSession = Depends(get_current_user)) -> dict[str, str]:
    """Allow an authenticated user to delete their own account and revoke their session (GDPR erasure)."""
    if current_user.jti and current_user.exp != float("inf"):
        TOKEN_BLACKLIST.revoke(current_user.jti, current_user.exp)

    target_username = None
    for username, data in _USER_REGISTRY.items():
        if data.get("user_id") == current_user.user_id:
            target_username = username
            break

    if target_username:
        del _USER_REGISTRY[target_username]

    return {
        "status": "success",
        "message": "Your user account and credentials have been permanently deleted.",
    }

