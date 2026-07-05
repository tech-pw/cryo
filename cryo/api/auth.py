"""Authentication and authorization for Cryo API.

Simple JWT-based authentication with roles:
- viewer: Read-only access
- editor: Can trigger jobs, create presets
- admin: Full access including destructive operations
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

# In production, load from environment or secret manager
JWT_SECRET = os.getenv("CRYO_JWT_SECRET", "change-me-in-production-via-env-var")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24

security = HTTPBearer()


class TokenData(BaseModel):
    """JWT token payload."""
    
    sub: str  # user identifier
    role: str  # viewer, editor, admin
    exp: datetime
    iat: datetime


class User(BaseModel):
    """Authenticated user."""
    
    username: str
    role: str
    
    def is_admin(self) -> bool:
        return self.role == "admin"
    
    def is_editor(self) -> bool:
        return self.role in ("editor", "admin")
    
    def is_viewer(self) -> bool:
        return self.role in ("viewer", "editor", "admin")


def create_access_token(username: str, role: str) -> str:
    """Create a JWT access token.
    
    Args:
        username: User identifier
        role: One of "viewer", "editor", "admin"
        
    Returns:
        JWT token string
    """
    now = datetime.now(UTC)
    exp = now + timedelta(hours=JWT_EXPIRY_HOURS)
    
    payload = {
        "sub": username,
        "role": role,
        "exp": exp.timestamp(),
        "iat": now.timestamp(),
    }
    
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> TokenData:
    """Verify and decode a JWT token.
    
    Args:
        token: JWT token string
        
    Returns:
        Decoded token data
        
    Raises:
        HTTPException: If token is invalid or expired
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        
        exp_timestamp = payload.get("exp")
        iat_timestamp = payload.get("iat")
        
        if not exp_timestamp or not iat_timestamp:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing timestamps",
            )
        
        token_data = TokenData(
            sub=payload["sub"],
            role=payload["role"],
            exp=datetime.fromtimestamp(exp_timestamp, tz=UTC),
            iat=datetime.fromtimestamp(iat_timestamp, tz=UTC),
        )
        
        # Check expiry
        if token_data.exp < datetime.now(UTC):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired",
            )
        
        return token_data
        
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e}",
        )


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> User:
    """Extract and verify the current user from the Authorization header.
    
    Args:
        credentials: Bearer token from Authorization header
        
    Returns:
        Authenticated user
        
    Raises:
        HTTPException: If token is invalid or missing
    """
    token = credentials.credentials
    token_data = verify_token(token)
    
    return User(username=token_data.sub, role=token_data.role)


def require_viewer(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Require viewer role or higher.
    
    Use this dependency for read-only endpoints.
    """
    if not user.is_viewer():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions: viewer role required",
        )
    return user


def require_editor(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Require editor role or higher.
    
    Use this dependency for endpoints that trigger jobs or modify presets.
    """
    if not user.is_editor():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions: editor role required",
        )
    return user


def require_admin(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Require admin role.
    
    Use this dependency for destructive operations or system configuration.
    """
    if not user.is_admin():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions: admin role required",
        )
    return user


# For backward compatibility during migration
def require_viewer_dict(
    user: Annotated[User, Depends(require_viewer)],
) -> dict:
    """Legacy wrapper that returns user as dict (for existing code)."""
    return {"username": user.username, "role": user.role}
