"""그룹·선언·인증·댓글·리액션 요청 스키마 (API 명세 §6).

응답은 서비스 계층이 dict 로 만든다 (core/response.py 의 주석 참고).
여기서 막는 것은 **형식**뿐이고, 정원·중복·잠금 같은 규칙은 서비스에서 본다.
"""

from __future__ import annotations

from datetime import date as date_type
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.group import MAX_MEMBERS, MIN_MEMBERS
from app.db.models.proof import REACTION_TYPES
from app.services.uploads import ALLOWED_CONTENT_TYPES

ReactionType = Literal["fire", "clap", "heart", "muscle"]
ContentType = Literal["image/jpeg", "image/png", "image/heic"]

# Literal 과 화이트리스트가 갈라지면 422 와 400 이 뒤섞인다. import 시점에 맞춰 둔다.
assert set(ReactionType.__args__) == set(REACTION_TYPES)  # type: ignore[attr-defined]
assert set(ContentType.__args__) == set(ALLOWED_CONTENT_TYPES)  # type: ignore[attr-defined]


class GroupCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=50)
    # 신청서 기준 3~6명. 기본값은 상한과 같게 둔다 (§6.1).
    max_members: int = Field(default=MAX_MEMBERS, ge=MIN_MEMBERS, le=MAX_MEMBERS)


class GroupJoinRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    invite_code: str = Field(min_length=4, max_length=12)


class OwnershipTransferRequest(BaseModel):
    user_id: int = Field(ge=1)


class DeclarationCreateRequest(BaseModel):
    """선언은 만든 뒤 고칠 수 없다. 그래서 입력 검증을 여기서 빡빡하게 한다."""

    date: date_type | None = None
    todo_ids: list[int] = Field(min_length=1, max_length=20)


class ProofCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    declaration_item_id: int = Field(ge=1)
    file_key: str = Field(min_length=1, max_length=500)
    caption: str | None = Field(default=None, max_length=200)


class PresignRequest(BaseModel):
    content_type: ContentType
    purpose: Literal["proof", "profile"] = "proof"


class CommentCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    content: str = Field(min_length=1, max_length=500)


class ReactionPutRequest(BaseModel):
    type: ReactionType
