"""규칙 초안 생성 → Gemini 검수 → 사용자 확인용 결과.

키 없음·검수 실패·쿼터 초과 시 규칙 초안을 유지한다.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from app.core.errors import AppError
from app.services.ai import llm, rules
from app.services.ai.schemas import Draft, GoalHint, ParseResult

LLM_TIMEOUT_SECONDS = 8.0
logger = logging.getLogger("p2j.ai")


async def parse(
    text: str, ref_date: date, goals: list[GoalHint], *, allow_llm: bool = True
) -> ParseResult:
    try:
        result = rules.parse(text, ref_date, goals)
    except Exception as exc:
        logger.exception("규칙 파서 예외")
        raise AppError("AI_UNAVAILABLE") from exc

    if not result.drafts:
        result = ParseResult(
            drafts=[Draft(title=text.strip()[:100], date=ref_date, confidence=0.0)],
            method="none",
            warnings=result.warnings,
        )

    if allow_llm:
        try:
            async with asyncio.timeout(LLM_TIMEOUT_SECONDS):
                reviewed = await llm.parse(text, ref_date, goals, rule_result=result)
                return reviewed
        except (TimeoutError, llm.LLMUnavailable) as exc:
            # 원문·응답·키는 기록하지 않는다.
            logger.info("Gemini 검수 실패, 규칙 초안 유지: %s", type(exc).__name__)

    return result
