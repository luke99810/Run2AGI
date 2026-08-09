from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import codentum_harness.model_gateway.smoke as smoke
import pytest
from codentum_contracts import (
    CostEstimate,
    CostLedger,
    ModelId,
    ModelResponse,
    ModelRouting,
    RoleId,
    Usage,
)
from codentum_contracts.interfaces import ModelRequest


def test_run_model_gateway_smoke_returns_safe_summary() -> None:
    gateway = FakeGateway()

    result = asyncio.run(
        smoke.run_model_gateway_smoke(
            gateway,
            provider="fake",
            role="coder",
            routing=ModelRouting(model="qwen-plus", effort="low"),
            grant_cny=0.05,
        )
    )

    assert result.provider == "fake"
    assert result.session_id == "session-1"
    assert result.model == "qwen-plus"
    assert result.role == "coder"
    assert result.stop_reason == "end"
    assert result.usage == {
        "cost_cny": 0.000003,
        "input_tokens": 1,
        "output_tokens": 1,
        "cached_input_tokens": 0,
    }
    assert result.ledger_total_cny == 0.000003
    assert result.text_preview == "codentum-smoke-ok"
    assert gateway.session.closed is True
    assert gateway.opened == [("coder", "qwen-plus", 0.05)]


def test_smoke_main_outputs_json_summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(smoke, "build_model_gateway", lambda _config: FakeGateway())

    code = smoke.main(
        (
            "--provider",
            "openai-compatible",
            "--base-url",
            "https://example.invalid/v1",
            "--model",
            "qwen-plus",
            "--input-price-cny-per-million",
            "1",
            "--output-price-cny-per-million",
            "2",
        )
    )

    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["provider"] == "openai-compatible"
    assert payload["text_preview"] == "codentum-smoke-ok"
    assert "api" not in captured.out.lower()
    assert "key" not in captured.out.lower()


def test_smoke_main_requires_pricing_by_default(capsys: pytest.CaptureFixture[str]) -> None:
    code = smoke.main(("--provider", "bailian", "--model", "qwen-plus"))

    captured = capsys.readouterr()
    assert code == 2
    assert "pricing is required" in captured.err


def test_smoke_main_reports_missing_api_key(capsys: pytest.CaptureFixture[str]) -> None:
    code = smoke.main(
        (
            "--provider",
            "bailian",
            "--model",
            "qwen-plus",
            "--input-price-cny-per-million",
            "1",
            "--output-price-cny-per-million",
            "2",
            "--api-key-env",
            "CODENTUM_TEST_MISSING_KEY",
        )
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "CODENTUM_TEST_MISSING_KEY" in captured.err
    assert "test-key" not in captured.err


class FakeGateway:
    def __init__(self) -> None:
        self.session = FakeSession()
        self.opened: list[tuple[RoleId, ModelId, float]] = []

    async def open(self, role: RoleId, routing: ModelRouting, grant_cny: float) -> FakeSession:
        self.opened.append((role, routing.model, grant_cny))
        return self.session

    async def estimate(self, routing: ModelRouting, req: ModelRequest) -> CostEstimate:
        return CostEstimate(estimated_cny=0.000003, upper_bound_cny=0.000006)

    async def ledger(self) -> CostLedger:
        return CostLedger(total_cny=0.000003, by_role={"coder": 0.000003}, by_model={}, since="now")


class FakeSession:
    def __init__(self) -> None:
        self.closed = False
        self.requests: list[ModelRequest] = []

    @property
    def session_id(self) -> str:
        return "session-1"

    @property
    def model(self) -> ModelId:
        return "qwen-plus"

    @property
    def role(self) -> RoleId:
        return "coder"

    async def invoke(self, req: ModelRequest) -> ModelResponse:
        self.requests.append(req)
        return ModelResponse(
            text="codentum-smoke-ok",
            tool_calls=(),
            stop_reason="end",
            usage=Usage(
                cost_cny=0.000003,
                input_tokens=1,
                output_tokens=1,
                cached_input_tokens=0,
            ),
        )

    def stream(self, req: ModelRequest) -> AsyncIterator[Any]:
        return _empty_stream(req)

    def spent_cny(self) -> float:
        return 0.000003

    async def close(self) -> None:
        self.closed = True


async def _empty_stream(req: ModelRequest) -> AsyncIterator[Any]:
    items: Sequence[Any] = ()
    for item in items:
        yield (req, item)
