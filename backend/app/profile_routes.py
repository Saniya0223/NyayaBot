from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.models import UserMemoryModel, UserModel
from app.db.session import get_db
from app.schemas.user_profile import (
    CaseHistoryItem, MemoryCreate, MemoryResponse, ProfileResponse, ProfileUpdate,
)
from app.services.user_context import case_history


router = APIRouter(prefix="/api/v1/profile", tags=["profile"])


@router.get("", response_model=ProfileResponse)
def get_profile(response: Response, user: UserModel = Depends(get_current_user)):
    response.headers["Cache-Control"] = "no-store"
    return user


@router.put("", response_model=ProfileResponse)
def update_profile(payload: ProfileUpdate, response: Response, db: Session = Depends(get_db),
                   user: UserModel = Depends(get_current_user)):
    for key, value in payload.model_dump(mode="json").items():
        setattr(user, key, value)
    db.commit()
    db.refresh(user)
    response.headers["Cache-Control"] = "no-store"
    return user


@router.get("/memories", response_model=list[MemoryResponse])
def list_memories(response: Response, db: Session = Depends(get_db),
                  user: UserModel = Depends(get_current_user)):
    response.headers["Cache-Control"] = "no-store"
    return db.query(UserMemoryModel).filter(UserMemoryModel.user_id == user.id).order_by(
        UserMemoryModel.updated_at.desc()).all()


@router.post("/memories", response_model=MemoryResponse, status_code=201)
def add_memory(payload: MemoryCreate, db: Session = Depends(get_db),
               user: UserModel = Depends(get_current_user)):
    if db.query(UserMemoryModel).filter(UserMemoryModel.user_id == user.id).count() >= 50:
        raise HTTPException(status_code=422, detail="Delete an old memory before adding another.")
    memory = UserMemoryModel(user_id=user.id, category=payload.category, text=payload.text)
    db.add(memory)
    db.commit()
    db.refresh(memory)
    return memory


@router.delete("/memories/{memory_id}", status_code=204)
def delete_memory(memory_id: str, db: Session = Depends(get_db),
                  user: UserModel = Depends(get_current_user)):
    memory = db.query(UserMemoryModel).filter(UserMemoryModel.id == memory_id,
                                              UserMemoryModel.user_id == user.id).first()
    if memory is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    db.delete(memory)
    db.commit()


@router.get("/case-history", response_model=list[CaseHistoryItem])
def list_case_history(response: Response, db: Session = Depends(get_db),
                      user: UserModel = Depends(get_current_user),
                      category: str | None = Query(default=None, max_length=50)):
    response.headers["Cache-Control"] = "no-store"
    return case_history(db, user.id, category=category)
