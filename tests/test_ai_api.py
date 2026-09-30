"""/ai/parse · /ai/quota · /todos/bulk — LLM 키 없음(규칙 폴백), Redis 없음(쿼터 건너뜀) 환경."""

from datetime import date

import pytest
from httpx import AsyncClient

from app.services.ai import llm, pipeline, quota
from app.services.ai.schemas import Draft, GoalHint, ParseResult


async def test_parse_falls_back_to_rules_without_llm(client: AsyncClient, auth_headers) -> None:
    gid = (
        await client.post("/v1/goals", json={"title": "주 3회 운동하기"}, headers=auth_headers)
    ).json()["data"]["goal_id"]

    r = await client.post(
        "/v1/ai/parse",
        json={"text": "내일 보고서 초안 쓰고 저녁에 운동하기", "reference_date": "2026-09-05"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["parse_id"].startswith("prs_")
    assert data["parse_method"] == "rules"
    assert [d["draft_id"] for d in data["drafts"]] == ["d1", "d2"]
    assert data["drafts"][0]["date"] == "2026-09-06"
    assert (
        data["drafts"][1]["goal_id"] == gid and data["drafts"][1]["goal_title"] == "주 3회 운동하기"
    )
    assert set(data["quota"]) == {"remaining_today", "reset_at"}
    assert data["quota"]["reset_at"].endswith("T04:00:00+09:00")


async def test_parse_none_method_returns_raw_text(
    client: AsyncClient, auth_headers, monkeypatch
) -> None:
    # 규칙 파서가 0건을 내는 경우를 강제
    monkeypatch.setattr(
        pipeline.rules, "parse", lambda text, ref, goals: ParseResult(drafts=[], method="rules")
    )
    r = await client.post("/v1/ai/parse", json={"text": "ㅁㄴㅇㄹ"}, headers=auth_headers)
    data = r.json()["data"]
    assert data["parse_method"] == "none"
    assert data["drafts"][0]["title"] == "ㅁㄴㅇㄹ" and data["drafts"][0]["confidence"] == 0.0


async def test_parse_uses_llm_when_available(
    client: AsyncClient, auth_headers, monkeypatch
) -> None:
    async def fake_llm(
        text: str, ref: date, goals: list[GoalHint], *, rule_result: ParseResult
    ) -> ParseResult:
        assert rule_result.method == "rules"
        assert rule_result.drafts[0].title == text
        return ParseResult(drafts=[Draft(title="LLM 결과", date=ref, confidence=0.9)], method="llm")

    monkeypatch.setattr(llm, "parse", fake_llm)
    r = await client.post("/v1/ai/parse", json={"text": "아무거나"}, headers=auth_headers)
    assert r.json()["data"]["parse_method"] == "llm"
    assert r.json()["data"]["drafts"][0]["title"] == "LLM 결과"


async def test_parse_llm_timeout_falls_back(client: AsyncClient, auth_headers, monkeypatch) -> None:
    import asyncio

    async def slow_llm(
        text: str, ref: date, goals: list[GoalHint], *, rule_result: ParseResult
    ) -> ParseResult:
        await asyncio.sleep(5)
        raise AssertionError("도달하면 안 됨")

    monkeypatch.setattr(llm, "parse", slow_llm)
    monkeypatch.setattr(pipeline, "LLM_TIMEOUT_SECONDS", 0.05)
    r = await client.post("/v1/ai/parse", json={"text": "내일 운동"}, headers=auth_headers)
    assert r.json()["data"]["parse_method"] == "rules"


async def test_parse_quota_exceeded_uses_rules_with_warning(
    client: AsyncClient, auth_headers, monkeypatch
) -> None:
    async def exhausted(user_id: int):
        return quota.QuotaState(30, 31, "2026-09-06T04:00:00+09:00"), False, True

    monkeypatch.setattr(quota, "consume", exhausted)
    r = await client.post("/v1/ai/parse", json={"text": "내일 운동"}, headers=auth_headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["parse_method"] == "rules"
    assert "quota_exceeded" in data["warnings"]
    assert data["quota"]["remaining_today"] == 0


async def test_parse_minute_limit_is_429(client: AsyncClient, auth_headers, monkeypatch) -> None:
    async def burst(user_id: int):
        return quota.QuotaState(30, 3, "x"), True, False

    monkeypatch.setattr(quota, "consume", burst)
    r = await client.post("/v1/ai/parse", json={"text": "내일 운동"}, headers=auth_headers)
    assert r.status_code == 429 and r.json()["error"]["code"] == "AI_QUOTA_EXCEEDED"


async def test_parse_validation(client: AsyncClient, auth_headers) -> None:
    r = await client.post("/v1/ai/parse", json={"text": "x" * 501}, headers=auth_headers)
    assert (
        r.status_code == 400
        and r.json()["error"]["details"]["text"] == "500자 이하로 입력해 주세요."
    )


async def test_quota_endpoint_without_redis(client: AsyncClient, auth_headers) -> None:
    r = await client.get("/v1/ai/quota", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["data"] == {
        "limit_per_day": 30,
        "used_today": 0,
        "remaining_today": 30,
        "reset_at": r.json()["data"]["reset_at"],
    }


# ---- /todos/bulk -------------------------------------------------------------------


async def test_bulk_creates_all(client: AsyncClient, auth_headers) -> None:
    gid = (await client.post("/v1/goals", json={"title": "운동"}, headers=auth_headers)).json()[
        "data"
    ]["goal_id"]
    r = await client.post(
        "/v1/todos/bulk",
        json={
            "source": "ai_suggested",
            "parse_id": "prs_8f3a2c",
            "items": [
                {"title": "보고서 초안 쓰기", "date": "2026-09-06", "estimated_minutes": 90},
                {"title": "헬스", "date": "2026-09-06", "goal_id": gid},
                {"title": "오늘 할 것"},
            ],
        },
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["created_count"] == 3
    assert [t["order"] for t in data["items"][:2]] == [1, 2]  # 같은 날짜 안에서 순서 증가
    assert data["items"][1]["goal_title"] == "운동"
    assert all(t["source"] == "ai_suggested" for t in data["items"])


async def test_bulk_is_all_or_nothing(client: AsyncClient, auth_headers) -> None:
    r = await client.post(
        "/v1/todos/bulk",
        json={"items": [{"title": "정상"}, {"title": "고아", "goal_id": 999999}]},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.json()["error"]["details"] == {"items[1].goal_id": "목표를 찾을 수 없어요."}
    today_items = (await client.get("/v1/todos", headers=auth_headers)).json()["data"]["items"]
    assert today_items == []  # 첫 항목도 저장되지 않았다

    r = await client.post("/v1/todos/bulk", json={"items": [{"title": ""}]}, headers=auth_headers)
    assert r.json()["error"]["details"] == {"items[0].title": "1자 이상 입력해 주세요."}

    r = await client.post("/v1/todos/bulk", json={"items": []}, headers=auth_headers)
    assert r.json()["error"]["details"] == {"items": "1개 이상이어야 해요."}


@pytest.mark.parametrize("n", [21])
async def test_bulk_max_20(client: AsyncClient, auth_headers, n: int) -> None:
    r = await client.post(
        "/v1/todos/bulk", json={"items": [{"title": "x"}] * n}, headers=auth_headers
    )
    assert r.status_code == 400 and r.json()["error"]["details"] == {"items": "20개 이하여야 해요."}
