from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import (
    clear_session_cookie,
    create_login_session,
    get_current_user,
    hash_password,
    revoke_current_session,
    set_session_cookie,
    verify_password,
)
from app.db.models import UserModel
from app.db.session import get_db
from app.schemas.auth import LoginRequest, SignupRequest, UserResponse


router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])


@router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest, response: Response, db: Session = Depends(get_db)):
    existing = (
        db.query(UserModel)
        .filter(func.lower(UserModel.email) == payload.email.casefold())
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    user = UserModel(
        full_name=payload.full_name,
        email=payload.email.casefold(),
        password_hash=hash_password(payload.password),
        phone=payload.phone,
        city=payload.city,
        state=payload.state,
    )
    db.add(user)
    try:
        db.flush()
        token, expires_at = create_login_session(db, user)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="An account with this email already exists"
        ) from exc
    set_session_cookie(response, token, expires_at)
    return user


@router.post("/login", response_model=UserResponse)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = (
        db.query(UserModel)
        .filter(func.lower(UserModel.email) == payload.email.casefold())
        .first()
    )
    if not verify_password(payload.password, user.password_hash if user else None):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    token, expires_at = create_login_session(db, user)
    set_session_cookie(response, token, expires_at)
    return user


@router.get("/me", response_model=UserResponse)
def me(response: Response, current_user: UserModel = Depends(get_current_user)):
    response.headers["Cache-Control"] = "no-store"
    return current_user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    revoke_current_session(request, db)
    clear_session_cookie(response)
