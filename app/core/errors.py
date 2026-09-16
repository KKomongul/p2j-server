"""오류 코드 체계 (API 명세 v1 §1.3·§1.4).

모든 실패 응답은 `{ "error": { "code", "message", "details" } }` 로 나간다.
`message` 는 모바일 스낵바에 그대로 노출되는 한국어 문장이다. 영어·기술 용어·스택 금지.

사용법:
    raise AppError("TODO_NOT_FOUND")                       # 카탈로그의 기본 문구
    raise AppError("VALIDATION_ERROR", details={"title": "할 일 이름을 입력하세요."})
    raise TodoNotFound()                                   # 자주 쓰는 것은 클래스로
"""

from __future__ import annotations

from typing import Any

# code -> (HTTP status, 기본 메시지). 명세 §1.4 표와 1:1 로 맞춘다.
# 새 코드를 추가하면 docs/specs/02-api-v1.md 표와 모바일 ApiErrorCode 에도 알린다.
ERROR_CATALOG: dict[str, tuple[int, str]] = {
    # 400
    "VALIDATION_ERROR": (400, "입력값을 확인해 주세요."),
    "WEAK_PASSWORD": (400, "비밀번호는 8자 이상으로 만들어 주세요."),
    # 401 — 모바일은 INVALID_CREDENTIALS 만 재발급 없이 폼에 표시한다 (§1.3)
    "INVALID_CREDENTIALS": (401, "이메일 또는 비밀번호가 맞지 않아요."),
    "TOKEN_EXPIRED": (401, "로그인이 만료됐어요. 다시 로그인해 주세요."),
    "UNAUTHORIZED": (401, "로그인이 필요해요."),
    # 403
    "FORBIDDEN": (403, "접근 권한이 없어요."),
    "NOT_GROUP_MEMBER": (403, "그룹 구성원만 볼 수 있어요."),
    "NOT_GROUP_ADMIN": (403, "그룹 관리자만 할 수 있어요."),
    # 404
    "NOT_FOUND": (404, "요청한 경로를 찾을 수 없어요."),
    "TODO_NOT_FOUND": (404, "할 일을 찾을 수 없어요."),
    "GOAL_NOT_FOUND": (404, "목표를 찾을 수 없어요."),
    "GROUP_NOT_FOUND": (404, "그룹을 찾을 수 없어요."),
    "DECLARATION_NOT_FOUND": (404, "선언을 찾을 수 없어요."),
    "POST_NOT_FOUND": (404, "게시물을 찾을 수 없어요."),
    "COMMENT_NOT_FOUND": (404, "댓글을 찾을 수 없어요."),
    "LOAD_CHECK_NOT_FOUND": (404, "안내 기록을 찾을 수 없어요."),
    "USER_NOT_FOUND": (404, "사용자를 찾을 수 없어요."),
    # 405 (명세 외 — 라우터 기본 응답 형식 통일용)
    "METHOD_NOT_ALLOWED": (405, "지원하지 않는 요청이에요."),
    # 409
    "EMAIL_ALREADY_EXISTS": (409, "이미 가입된 이메일이에요."),
    "ALREADY_MEMBER": (409, "이미 참여한 그룹이에요."),
    "DECLARATION_ALREADY_EXISTS": (409, "오늘은 이미 선언했어요."),
    "TODO_ALREADY_DONE": (409, "이미 완료한 할 일이에요."),
    "TODO_NOT_DONE": (409, "완료한 할 일에만 사진을 올릴 수 있어요."),
    # 413 / 415
    "FILE_TOO_LARGE": (413, "사진은 10MB 이하로 올려 주세요."),
    "UNSUPPORTED_FILE_TYPE": (415, "jpg, png, heic 사진만 올릴 수 있어요."),
    # 422
    "DECLARED_TODO_LOCKED": (422, "그룹에 선언한 항목은 오늘 수정할 수 없어요."),
    "DECLARATION_CLOSED": (422, "선언 시간이 지났어요."),
    "DECLARATION_EMPTY": (422, "선언할 할 일을 하나 이상 골라 주세요."),
    "PROOF_ALREADY_EXISTS": (422, "이미 인증한 항목이에요."),
    "GROUP_FULL": (422, "그룹 정원이 가득 찼어요."),
    "INVALID_INVITE_CODE": (422, "초대 코드를 다시 확인해 주세요."),
    "INVITE_CODE_EXPIRED": (422, "만료된 초대 코드예요."),
    "ADMIN_MUST_TRANSFER": (422, "관리자를 다른 구성원에게 넘긴 뒤 나갈 수 있어요."),
    "CANNOT_KICK_SELF": (422, "자기 자신은 내보낼 수 없어요."),
    # 429
    "AI_QUOTA_EXCEEDED": (429, "AI 요청이 너무 잦아요. 잠시 후 다시 시도해 주세요."),
    "RATE_LIMITED": (429, "요청이 너무 많아요. 잠시 후 다시 시도해 주세요."),
    # 5xx
    "INTERNAL_ERROR": (500, "서버에 문제가 생겼어요. 잠시 후 다시 시도해 주세요."),
    "AI_UNAVAILABLE": (503, "지금은 AI 분석을 사용할 수 없어요. 직접 입력해 주세요."),
    "STORAGE_UNAVAILABLE": (503, "사진을 저장하지 못했어요. 나중에 다시 올려 주세요."),
    # 헬스체크 전용 (Railway 재시작 판단용)
    "HEALTH_CHECK_FAILED": (503, "일부 서비스에 연결할 수 없어요."),
}


class AppError(Exception):
    """API 명세의 오류 코드를 그대로 담는 예외. 핸들러가 `{error}` 형식으로 바꾼다."""

    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        status: int | None = None,
    ) -> None:
        if code not in ERROR_CATALOG and status is None:
            raise ValueError(
                f"카탈로그에 없는 오류 코드: {code}. ERROR_CATALOG 에 먼저 등록하세요."
            )
        default_status, default_message = ERROR_CATALOG.get(code, (status or 500, ""))
        self.code = code
        self.status = status or default_status
        self.message = message or default_message
        self.details: dict[str, Any] = details or {}
        super().__init__(f"{self.status} {self.code}: {self.message}")

    def to_body(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


# ---- 자주 쓰는 예외를 이름으로 --------------------------------------------------


class Unauthorized(AppError):
    def __init__(self, message: str | None = None) -> None:
        super().__init__("UNAUTHORIZED", message)


class TokenExpired(AppError):
    def __init__(self) -> None:
        super().__init__("TOKEN_EXPIRED")


class InvalidCredentials(AppError):
    def __init__(self) -> None:
        super().__init__("INVALID_CREDENTIALS")


class Forbidden(AppError):
    def __init__(self) -> None:
        super().__init__("FORBIDDEN")


class NotFound(AppError):
    def __init__(self, code: str = "NOT_FOUND") -> None:
        super().__init__(code)


class TodoNotFound(NotFound):
    def __init__(self) -> None:
        super().__init__("TODO_NOT_FOUND")


class GoalNotFound(NotFound):
    def __init__(self) -> None:
        super().__init__("GOAL_NOT_FOUND")


class DeclaredTodoLocked(AppError):
    def __init__(self) -> None:
        super().__init__("DECLARED_TODO_LOCKED")


class FieldValidationError(AppError):
    """서비스 계층에서 던지는 필드 검증 오류. details 는 필드명 → 한국어 메시지."""

    def __init__(self, details: dict[str, str], message: str | None = None) -> None:
        super().__init__("VALIDATION_ERROR", message, details=details)


# ---- Pydantic 오류 → 한국어 ---------------------------------------------------

# Pydantic v2 오류 타입(type) → 사용자 문구. ctx 값이 있으면 포맷에 넣는다.
# 없는 타입은 DEFAULT_FIELD_MESSAGE 로 떨어진다. 영어가 새어 나가지 않게 하는 게 목적.
KO_MESSAGES: dict[str, str] = {
    "missing": "필수 항목이에요.",
    "string_type": "문자로 입력해 주세요.",
    "string_too_short": "{min_length}자 이상 입력해 주세요.",
    "string_too_long": "{max_length}자 이하로 입력해 주세요.",
    "string_pattern_mismatch": "형식이 올바르지 않아요.",
    "int_type": "숫자로 입력해 주세요.",
    "int_parsing": "숫자로 입력해 주세요.",
    "int_from_float": "정수로 입력해 주세요.",
    "float_type": "숫자로 입력해 주세요.",
    "float_parsing": "숫자로 입력해 주세요.",
    "bool_type": "참/거짓 값이어야 해요.",
    "bool_parsing": "참/거짓 값이어야 해요.",
    "greater_than": "{gt}보다 커야 해요.",
    "greater_than_equal": "{ge} 이상이어야 해요.",
    "less_than": "{lt}보다 작아야 해요.",
    "less_than_equal": "{le} 이하여야 해요.",
    "enum": "선택할 수 없는 값이에요.",
    "literal_error": "선택할 수 없는 값이에요.",
    "date_type": "날짜 형식(YYYY-MM-DD)으로 입력해 주세요.",
    "date_parsing": "날짜 형식(YYYY-MM-DD)으로 입력해 주세요.",
    "date_from_datetime_parsing": "날짜 형식(YYYY-MM-DD)으로 입력해 주세요.",
    "datetime_parsing": "시각 형식이 올바르지 않아요.",
    "list_type": "목록 형식이어야 해요.",
    "too_short": "{min_length}개 이상이어야 해요.",
    "too_long": "{max_length}개 이하여야 해요.",
    "dict_type": "객체 형식이어야 해요.",
    "model_type": "객체 형식이어야 해요.",
    "model_attributes_type": "객체 형식이어야 해요.",
    "extra_forbidden": "허용되지 않는 항목이에요.",
    "json_invalid": "요청 본문이 올바른 JSON 이 아니에요.",
    "value_error": "형식이 올바르지 않아요.",
    "uuid_parsing": "형식이 올바르지 않아요.",
}
DEFAULT_FIELD_MESSAGE = "값을 확인해 주세요."


def pydantic_error_to_message(error: dict[str, Any]) -> str:
    """Pydantic 오류 항목 하나를 한국어 문구로 바꾼다."""
    template = KO_MESSAGES.get(error.get("type", ""), DEFAULT_FIELD_MESSAGE)
    ctx = error.get("ctx") or {}
    try:
        return template.format(**ctx)
    except (KeyError, IndexError):
        return DEFAULT_FIELD_MESSAGE


def pydantic_loc_to_field(loc: tuple[Any, ...]) -> str:
    """('body', 'items', 1, 'title') → 'items[1].title'. 첫 요소(body/query/path)는 뗀다."""
    parts = list(loc)
    if parts and parts[0] in ("body", "query", "path", "header", "cookie"):
        parts = parts[1:]
    # JSON 자체가 깨진 경우 loc 은 ('body', <바이트 위치>) 다. 필드가 아니므로 'body' 로 묶는다.
    if not parts or (len(parts) == 1 and isinstance(parts[0], int)):
        return "body"
    out = ""
    for part in parts:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else str(part)
    return out
