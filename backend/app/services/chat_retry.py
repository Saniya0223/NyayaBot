"""Private retry checkpoints in existing session messages, keyed by response ID."""

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.db.models import ChatCaseSessionModel
from app.schemas.chat import ChatTurnResponse


RETRY_STATE_KEY = "_llm_retry"


def profile_fingerprint(profile_data: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(profile_data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def response_checkpoint(
    response: ChatTurnResponse, user_message_id: str,
    prior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if response._retry_state is not None:
        checkpoint = deepcopy(response._retry_state)
        checkpoint.update(status="READY", user_message_id=user_message_id)
        if prior and "reply_override" in prior:
            checkpoint["reply_override"] = prior["reply_override"]
        return checkpoint
    return {
        "status": "DONE", "user_message_id": user_message_id,
        "completed_response": response.model_dump(mode="json"),
    }


def claim_retry(
    db: Session, record: ChatCaseSessionModel, message_id: str,
) -> tuple[dict[str, Any], datetime | None]:
    message = next((item for item in record.messages_data if item.get("id") == message_id), None)
    checkpoint = (message or {}).get(RETRY_STATE_KEY)
    if not checkpoint:
        raise HTTPException(409, "This response has no saved retry stage. Send the original message again.")
    if checkpoint["status"] == "RUNNING":
        raise HTTPException(409, "This turn is already being retried.")
    if record.messages_data[-1].get("id") != message_id or checkpoint.get("profile_fingerprint") != profile_fingerprint(record.profile_data):
        raise HTTPException(409, "The case changed after this failed turn. Send a new message instead.")
    if checkpoint["status"] == "DONE":
        return deepcopy(checkpoint), None

    messages = deepcopy(record.messages_data)
    next(item for item in messages if item.get("id") == message_id)[RETRY_STATE_KEY]["status"] = "RUNNING"
    claimed_at = datetime.utcnow()
    # Compare-and-swap on the existing session revision. Concurrent requests,
    # including requests served by another worker, cannot claim the same turn.
    result = db.execute(update(ChatCaseSessionModel).where(
        ChatCaseSessionModel.case_id == record.case_id,
        ChatCaseSessionModel.user_id == record.user_id,
        ChatCaseSessionModel.updated_at == record.updated_at,
    ).values(messages_data=messages, updated_at=claimed_at).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "The turn changed or is already being retried.")
    db.commit()
    db.expire_all()
    return deepcopy(checkpoint), claimed_at


def reserve_completion(db: Session, record: ChatCaseSessionModel, claimed_at: datetime) -> None:
    # Hold the write reservation until the existing save commits both state and
    # replacement message. Do not overwrite a newer turn or document update.
    result = db.execute(update(ChatCaseSessionModel).where(
        ChatCaseSessionModel.case_id == record.case_id,
        ChatCaseSessionModel.user_id == record.user_id,
        ChatCaseSessionModel.updated_at == claimed_at,
    ).values(updated_at=datetime.utcnow()).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "The case changed while retrying. No retry state was applied.")


def release_claim(
    db: Session, record: ChatCaseSessionModel, message_id: str, claimed_at: datetime,
) -> None:
    db.rollback()
    db.refresh(record)
    messages = deepcopy(record.messages_data)
    message = next((item for item in messages if item.get("id") == message_id), None)
    if not message or record.updated_at != claimed_at:
        return
    message[RETRY_STATE_KEY]["status"] = "READY"
    db.execute(update(ChatCaseSessionModel).where(
        ChatCaseSessionModel.case_id == record.case_id,
        ChatCaseSessionModel.user_id == record.user_id,
        ChatCaseSessionModel.updated_at == claimed_at,
    ).values(messages_data=messages, updated_at=datetime.utcnow()).execution_options(synchronize_session=False))
    db.commit()
