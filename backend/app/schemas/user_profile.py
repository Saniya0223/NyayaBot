from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProfileUpdate(BaseModel):
    full_name: str = Field(min_length=2, max_length=255)
    date_of_birth: date
    phone: str | None = Field(default=None, max_length=20)
    state: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    pin_code: str | None = Field(default=None, pattern=r"^[1-9][0-9]{5}$")
    full_address: str | None = Field(default=None, max_length=500)
    preferred_language: Literal["english", "hindi", "hinglish"] | None = None

    @field_validator("full_name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if len(value) < 2:
            raise ValueError("Full name is required")
        return value

    @field_validator("date_of_birth")
    @classmethod
    def valid_dob(cls, value: date) -> date:
        if value > date.today() or value.year < 1900:
            raise ValueError("Enter a valid date of birth")
        return value

    @field_validator("phone", "state", "city", "full_address")
    @classmethod
    def clean_optional(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class ProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    full_name: str
    date_of_birth: str | None
    email: str
    phone: str | None
    state: str | None
    city: str | None
    pin_code: str | None
    full_address: str | None
    preferred_language: str | None


class MemoryCreate(BaseModel):
    category: Literal["preference", "recurring", "explicit"]
    text: str = Field(min_length=3, max_length=500)

    @field_validator("text")
    @classmethod
    def clean_text(cls, value: str) -> str:
        value = " ".join(value.split())
        if len(value) < 3:
            raise ValueError("Memory is too short")
        return value


class MemoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    category: str
    text: str
    created_at: datetime


class CaseHistoryItem(BaseModel):
    case_id: str
    title: str
    category: str
    status: str
    summary: str | None = None
