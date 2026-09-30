"""2단계 폴백 — 규칙 기반 파서 (§6.1). LLM 이 죽었을 때 유일한 방어선이다.

하는 일:
1. 문장 분리: 줄바꿈 · 쉼표 · "그리고" · "하고" · 마침표
2. 한국어 상대 날짜: 오늘 · 내일 · 모레 · 이번 주 X요일 · 다음 주 X요일 · N일 뒤 · M월 D일
3. 시간대 힌트: 아침/오전 · 점심/오후 · 저녁/밤
4. 소요 시간: N분 · N시간 · N시간 반 · 한 시간
5. 목표 매칭: 목표 제목 토큰이 문장에 들어 있으면 그 목표 (단순 포함 매칭, confidence 0.6)

박영준님이 Colab 에서 검증한 정규식이 생기면 이 파일을 통째로 교체해도 된다.
인터페이스는 `parse(text, ref_date, goals) -> ParseResult` 하나다.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from app.services.ai.schemas import Draft, GoalHint, ParseResult, TimeHint

WEEKDAYS = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
KOREAN_NUMBERS = {
    "한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8,
    "아홉": 9, "열": 10, "하나": 1, "둘": 2, "셋": 3, "넷": 4,
}  # fmt: skip

_SPLIT = re.compile(r"\n|,|，|\.\s|;|(?<=\S)\s+그리고\s+|(?<=\S)\s+하고\s+|(?<=\S)\s+그다음\s+")
_RELATIVE = re.compile(r"(오늘|내일|모레|글피)")
_WEEKDAY = re.compile(r"(이번\s*주|다음\s*주|담주|이번주|다음주)?\s*([월화수목금토일])요일")
_DAYS_LATER = re.compile(r"(\d+)\s*일\s*(뒤|후)")
_MONTH_DAY = re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_MINUTES = re.compile(r"(\d+)\s*분(?:간)?")
_HOURS = re.compile(
    r"(\d+|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*시간\s*(?:(반)|(\d+)\s*분)?(?:간)?"
)
_TIME_HINTS: list[tuple[re.Pattern[str], TimeHint]] = [
    (re.compile(r"아침|오전|새벽"), "morning"),
    (re.compile(r"점심|오후|낮"), "afternoon"),
    (re.compile(r"저녁|밤|퇴근\s*후|자기\s*전"), "evening"),
]
_TRAILING = re.compile(r"\s*(에|에서|은|는|도|을|를|이|가)?\s*$")
_LEADING = re.compile(r"^\s*(에|에서|은|는|도|을|를|이|가)\s+")


# 동사 연결형 "-고 " (쓰고 · 운동하고 · 먹고) 를 "-기, " 로 바꿔 문장을 끊는다. "그리고" 는 제외.
_VERB_GO = re.compile(r"([가-힣])(?<!그리)고\s+(?=\S)")
_STANDALONE_HAGO = re.compile(r"(?<=\S)\s+하고\s+(?=\S)")


def normalize(text: str) -> str:
    text = _STANDALONE_HAGO.sub(", ", text)  # "A 하고 B" (접속사)
    return _VERB_GO.sub(r"\1기, ", text)  # "보고서 쓰고 운동" → "보고서 쓰기, 운동"


def split_sentences(text: str) -> list[str]:
    parts = [p.strip(" \t\r-·•") for p in _SPLIT.split(normalize(text)) if p]
    return [p for p in parts if p and p not in ("그리고", "하고", "그다음")]


def extract_date(sentence: str, ref: date) -> tuple[date | None, str, list[str]]:
    """(날짜, 날짜 표현을 뗀 문장, warnings). 못 찾으면 (None, 원문, [])."""
    warnings: list[str] = []

    m = _MONTH_DAY.search(sentence)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        try:
            target = date(ref.year, month, day)
        except ValueError:
            return None, sentence, ["date_ambiguous"]
        if target < ref:
            try:
                target = date(ref.year + 1, month, day)
            except ValueError:  # e.g. February 29 followed by a non-leap year
                return None, sentence, ["date_ambiguous"]
            warnings.append("date_ambiguous")
        return target, _strip(sentence, m), warnings

    m = _DAYS_LATER.search(sentence)
    if m:
        offset = int(m.group(1))
        if offset > 365:
            return None, sentence, ["date_ambiguous"]
        return ref + timedelta(days=offset), _strip(sentence, m), warnings

    m = _WEEKDAY.search(sentence)
    if m:
        target_wd = WEEKDAYS[m.group(2)]
        monday = ref - timedelta(days=ref.weekday())
        candidate = monday + timedelta(days=target_wd)
        prefix = (m.group(1) or "").replace(" ", "")
        if prefix in ("다음주", "담주"):
            candidate += timedelta(days=7)
        elif prefix == "" and candidate < ref:
            candidate += timedelta(days=7)  # "금요일에" 만 말하면 다가오는 금요일
        if candidate < ref:
            warnings.append("date_in_past")
            candidate = ref
        return candidate, _strip(sentence, m), warnings

    m = _RELATIVE.search(sentence)
    if m:
        offset = {"오늘": 0, "내일": 1, "모레": 2, "글피": 3}[m.group(1)]
        return ref + timedelta(days=offset), _strip(sentence, m), warnings

    return None, sentence, warnings


def extract_time_hint(sentence: str) -> tuple[TimeHint | None, str]:
    for pattern, hint in _TIME_HINTS:
        m = pattern.search(sentence)
        if m:
            return hint, _strip(sentence, m)
    return None, sentence


def extract_minutes(sentence: str) -> tuple[int | None, str]:
    m = _HOURS.search(sentence)
    if m:
        raw = m.group(1)
        hours = KOREAN_NUMBERS.get(raw, None) if not raw.isdigit() else int(raw)
        if hours is not None:
            minutes = hours * 60 + (30 if m.group(2) else int(m.group(3) or 0))
            return max(1, min(minutes, 1440)), _strip(sentence, m)
    m = _MINUTES.search(sentence)
    if m:
        return max(1, min(int(m.group(1)), 1440)), _strip(sentence, m)
    return None, sentence


def match_goal(sentence: str, goals: list[GoalHint]) -> GoalHint | None:
    """목표 제목의 2글자 이상 토큰이 문장에 들어 있으면 매칭. 가장 많이 겹치는 목표."""
    best: tuple[int, GoalHint] | None = None
    for goal in goals:
        tokens = [t for t in re.split(r"[\s·,/]+", goal.title) if len(t) >= 2]
        score = sum(1 for t in tokens if t in sentence)
        if score and (best is None or score > best[0]):
            best = (score, goal)
    return best[1] if best else None


def _strip(sentence: str, m: re.Match[str]) -> str:
    # Remove a particle only immediately after the extracted date/time expression.
    # Stripping title endings corrupts words such as "요가" and "종이".
    suffix = re.sub(r"^(?:에|은|는|도|을|를)(?=\s|$)", "", sentence[m.end() :])
    return re.sub(r"\s{2,}", " ", sentence[: m.start()] + " " + suffix).strip()


def parse(text: str, ref_date: date, goals: list[GoalHint]) -> ParseResult:
    drafts: list[Draft] = []
    warnings: set[str] = set()
    # 첫 문장에서 잡은 날짜를 뒤 문장이 물려받는다: "내일 A 하고 B" → 둘 다 내일
    inherited: date | None = None

    for sentence in split_sentences(text):
        day, rest, w = extract_date(sentence, ref_date)
        warnings.update(w)
        if day is None:
            day = inherited or ref_date
        else:
            inherited = day
        hint, rest = extract_time_hint(rest)
        minutes, rest = extract_minutes(rest)
        goal = match_goal(rest, goals)
        title = rest.strip(" .,") or sentence
        if not title:
            continue
        drafts.append(
            Draft(
                title=title[:100],
                date=day,
                time_hint=hint,
                goal_id=goal.goal_id if goal else None,
                goal_title=goal.title if goal else None,
                estimated_minutes=minutes or (goal.estimated_minutes if goal else None),
                confidence=0.6 if goal else 0.5,
            )
        )

    return ParseResult(drafts=drafts, method="rules", warnings=sorted(warnings))
