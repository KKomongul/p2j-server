import json
from datetime import date
from types import SimpleNamespace

import httpx
import pytest

from app.services.ai import llm, pipeline, rules
from app.services.ai.schemas import GoalHint

REF = date(2026, 9, 30)
TEXT = "내일 운동 30분"


def mock_gemini(monkeypatch, handler):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        llm,
        "get_settings",
        lambda: SimpleNamespace(
            gemini_api_key="test-key",
            gemini_model="gemini-2.5-flash-lite",
        ),
    )
    monkeypatch.setattr(
        llm.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(handler),
            **kwargs,
        ),
    )


def response(drafts):
    return {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {
                    "parts": [
                        {"text": json.dumps({"drafts": drafts, "warnings": []})},
                    ]
                },
            }
        ]
    }


async def test_review_receives_rule_drafts_and_returns_validated_result(monkeypatch):
    goals = [GoalHint(goal_id=12, title="운동")]
    baseline = rules.parse(TEXT, REF, goals)

    def handler(request):
        assert request.url.host == "generativelanguage.googleapis.com"
        assert "test-key" not in str(request.url)
        assert request.headers["x-goog-api-key"] == "test-key"
        body = json.loads(request.content)
        content = json.loads(body["contents"][0]["parts"][0]["text"])
        assert content["original_text"] == TEXT
        assert content["rule_drafts"] == [d.model_dump(mode="json") for d in baseline.drafts]
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        return httpx.Response(
            200,
            json=response(
                [
                    {
                        "title": "운동하기",
                        "date": "2026-10-01",
                        "goal_id": 12,
                        "estimated_minutes": 30,
                        "confidence": 0.9,
                    }
                ]
            ),
        )

    mock_gemini(monkeypatch, handler)
    reviewed = await pipeline.parse(TEXT, REF, goals)
    assert reviewed.method == "llm"
    assert reviewed.drafts[0].title == "운동하기"
    assert reviewed.drafts[0].goal_title == "운동"
    assert reviewed.drafts[0].estimated_minutes == 30


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500])
async def test_http_failures_preserve_rules(monkeypatch, status):
    mock_gemini(monkeypatch, lambda request: httpx.Response(status, text="sensitive-response"))
    result = await pipeline.parse(TEXT, REF, [])
    assert result == rules.parse(TEXT, REF, [])


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"candidates": []},
        response([]),
        response([{"title": "bad"}]),
        {"candidates": [{"finishReason": "MAX_TOKENS"}]},
    ],
)
async def test_invalid_or_blocked_response_preserves_rules(monkeypatch, payload):
    mock_gemini(monkeypatch, lambda request: httpx.Response(200, json=payload))
    result = await pipeline.parse(TEXT, REF, [])
    assert result == rules.parse(TEXT, REF, [])


async def test_network_timeout_preserves_rules(monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout("request detail must not leak")

    mock_gemini(monkeypatch, handler)
    assert await pipeline.parse(TEXT, REF, []) == rules.parse(TEXT, REF, [])


async def test_quota_skip_does_not_call_gemini(monkeypatch):
    async def unexpected(*args, **kwargs):
        raise AssertionError("Gemini must not be called")

    monkeypatch.setattr(llm, "parse", unexpected)
    assert await pipeline.parse(TEXT, REF, [], allow_llm=False) == rules.parse(TEXT, REF, [])


async def test_bad_dates_and_unknown_goals_are_sanitized(monkeypatch):
    mock_gemini(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json=response(
                [
                    {
                        "title": "운동하기",
                        "date": "invalid",
                        "goal_id": 999,
                        "estimated_minutes": -10,
                        "confidence": 0.8,
                    }
                ]
            ),
        ),
    )
    result = await pipeline.parse(TEXT, REF, [])
    assert result.drafts[0].date == REF
    assert result.drafts[0].goal_id is None
    assert result.drafts[0].estimated_minutes is None
    assert "date_ambiguous" in result.warnings
