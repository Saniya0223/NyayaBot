import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response, status
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import AuthSessionModel, UserModel
from app.db.session import get_db


password_hasher = PasswordHash.recommended()
# Equalizes the expensive password verification path for an unknown email.
_DUMMY_PASSWORD_HASH = password_hasher.hash(secrets.token_urlsafe(32))


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    candidate_hash = password_hash or _DUMMY_PASSWORD_HASH
    try:
        return password_hasher.verify(password, candidate_hash) and password_hash is not None
    except Exception:
        return False


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_login_session(db: Session, user: UserModel) -> tuple[str, datetime]:
    now = datetime.utcnow()
    expires_at = now + timedelta(hours=settings.AUTH_SESSION_TTL_HOURS)
    raw_token = secrets.token_urlsafe(48)
    db.query(AuthSessionModel).filter(AuthSessionModel.expires_at <= now).delete(
        synchronize_session=False
    )
    db.add(
        AuthSessionModel(
            user_id=user.id,
            token_hash=_token_digest(raw_token),
            expires_at=expires_at,
        )
    )
    db.commit()
    return raw_token, expires_at


def set_session_cookie(response: Response, token: str, expires_at: datetime) -> None:
    same_site = settings.AUTH_COOKIE_SAMESITE
    if same_site not in {"lax", "strict", "none"}:
        same_site = "lax"
    response.set_cookie(
        key=settings.AUTH_SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite=same_site,
        expires=expires_at.replace(tzinfo=timezone.utc),
        max_age=settings.AUTH_SESSION_TTL_HOURS * 60 * 60,
        path="/api/v1",
        domain=settings.AUTH_COOKIE_DOMAIN,
    )
    response.headers["Cache-Control"] = "no-store"


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.AUTH_SESSION_COOKIE_NAME,
        path="/api/v1",
        domain=settings.AUTH_COOKIE_DOMAIN,
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite=settings.AUTH_COOKIE_SAMESITE,
    )
    response.headers["Cache-Control"] = "no-store"


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> UserModel:
    token = request.cookies.get(settings.AUTH_SESSION_COOKIE_NAME)
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
    )
    if not token:
        raise credentials_error

    session = (
        db.query(AuthSessionModel)
        .filter(AuthSessionModel.token_hash == _token_digest(token))
        .first()
    )
    if not session:
        raise credentials_error
    if session.expires_at <= datetime.utcnow():
        db.delete(session)
        db.commit()
        raise credentials_error
    if not session.user:
        raise credentials_error
    return session.user


def revoke_current_session(request: Request, db: Session) -> None:
    token = request.cookies.get(settings.AUTH_SESSION_COOKIE_NAME)
    if not token:
        return
    db.query(AuthSessionModel).filter(
        AuthSessionModel.token_hash == _token_digest(token)
    ).delete(synchronize_session=False)
    db.commit()
