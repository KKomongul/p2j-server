"""규칙 파서 (2단계 폴백). LLM 없이도 이 정도는 잡아야 한다."""

from datetime import date

from app.services.ai import rules
from app.services.ai.schemas import GoalHint

REF = date(2026, 9, 5)  # 토요일
GOALS = [
    GoalHint(goal_id=12, title="주 3회 운동하기", estimated_minutes=60),
    GoalHint(goal_id=13, title="토익 단어 매일 30개"),
]


def test_splits_and_inherits_date() -> None:
    r = rules.parse("내일 오전에 보고서 초안 쓰고 저녁에 헬스, 토익 단어도 30개", REF, GOALS)
    assert r.method == "rules"
    titles = [d.title for d in r.drafts]
    assert len(r.drafts) == 3, titles
    assert all(d.date == date(2026, 9, 6) for d in r.drafts)  # "내일" 을 뒤 문장이 물려받음
    assert r.drafts[0].time_hint == "morning"
    assert r.drafts[1].time_hint == "evening"
    assert "보고서 초안" in titles[0]
    assert r.drafts[2].goal_id == 13  # "토익 단어" 매칭


def test_relative_dates() -> None:
    assert rules.extract_date("모레 치과", REF)[0] == date(2026, 9, 7)
    assert rules.extract_date("3일 뒤 제출", REF)[0] == date(2026, 9, 8)
    assert rules.extract_date("10월 1일 발표", REF)[0] == date(2026, 10, 1)
    # 이번 주 월요일은 이미 지났다 → date_in_past 경고 + 오늘로 보정
    day, _, warnings = rules.extract_date("이번 주 월요일 회의", REF)
    assert day == REF and "date_in_past" in warnings
    assert rules.extract_date("다음 주 월요일 회의", REF)[0] == date(2026, 9, 7)
    assert rules.extract_date("화요일에 병원", REF)[0] == date(2026, 9, 8)  # 다가오는 화요일
    assert rules.extract_date("그냥 할 일", REF)[0] is None


def test_minutes() -> None:
    assert rules.extract_minutes("한 시간 반 공부")[0] == 90
    assert rules.extract_minutes("2시간 코딩")[0] == 120
    assert rules.extract_minutes("30분 스트레칭")[0] == 30
    assert rules.extract_minutes("스트레칭")[0] is None


def test_goal_match_fills_estimated_minutes() -> None:
    r = rules.parse("운동하기", REF, GOALS)
    assert r.drafts[0].goal_id == 12
    assert r.drafts[0].estimated_minutes == 60  # 목표의 기본 소요 시간
    assert r.drafts[0].confidence == 0.6


def test_empty_after_split_yields_no_drafts() -> None:
    r = rules.parse("   ,  ,  ", REF, [])
    assert r.drafts == []


def test_compound_duration_and_title_preservation() -> None:
    result = rules.parse("오늘 1시간 30분 요가, 내일 두 시간 반 독서", REF, [])
    assert [(d.title, d.estimated_minutes) for d in result.drafts] == [("요가", 90), ("독서", 150)]
    assert rules.parse("내일 종이 접기", REF, []).drafts[0].title == "종이 접기"
    assert rules.extract_minutes("0시간 스트레칭")[0] == 1
    assert rules.extract_minutes("다섯 시간 공부")[0] == 300


def test_ambiguous_dates_do_not_crash_pipeline() -> None:
    result = rules.parse("2월 29일 운동", date(2024, 3, 1), [])
    assert result.drafts[0].date == date(2024, 3, 1)
    assert "date_ambiguous" in result.warnings
    result = rules.parse("999999999999999999일 뒤 운동", REF, [])
    assert result.drafts[0].date == REF
    assert "date_ambiguous" in result.warnings
