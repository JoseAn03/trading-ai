"""Fase 6: explicacion determinista, LLM validado y fallback seguro."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from src.config.settings import Settings
from src.data.store import Store
from src.delivery.telegram import deliver
from src.llm.client import ChatClient, LLMConfig, LLMError, LLMResponse
from src.llm.guard import guard_explanation, invented_numbers
from src.paper_trading.shadow import PaperStats
from src.quant.risk import RiskDecision
from src.reporting.daily_report import LLMReport
from src.reporting.explain import build_deterministic, build_payload, explain_daily
from src.schemas.enums import Signal
from src.schemas.signal import SignalBatch
from tests.conftest import make_signal

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path) -> Store:
    instance = Store(tmp_path / "explain.db")
    instance.init_schema()
    return instance


def _batch():
    return SignalBatch(
        generated_at=NOW,
        horizon="24h",
        signals=[
            make_signal(asset="BTC", p_net=0.57, ts=NOW),
            make_signal(asset="ETH", signal=Signal.NO_TRADE, actionable=False, p_net=0.48, size=0.0, ts=NOW),
        ],
        model_version="test-v1",
        code_version="test-sha",
    )


def _paper() -> PaperStats:
    return PaperStats(
        trades=0,
        open_trades=0,
        win_rate=0.0,
        equity=1.0,
        total_return_pct=0.0,
        avg_net_return=0.0,
        max_drawdown_pct=0.0,
    )


def _decisions(batch: SignalBatch) -> dict[str, RiskDecision]:
    return {
        s.asset: RiskDecision(
            allowed=s.is_actionable,
            reasons=[],
            suggested_size_pct=s.risk.suggested_size_pct,
        )
        for s in batch.signals
    }


def _llm_settings(**overrides) -> Settings:
    base = {
        "llm_enabled": True,
        "llm_api_key": "test-key",
        "llm_model": "fake-model",
        "llm_base_url": "https://fake.local/v1",
        "llm_monthly_budget_usd": 1.0,
    }
    return Settings(**{**base, **overrides})


def _body(**overrides) -> dict:
    base = {
        "subject": "Reporte Trading AI — 22 Oct 2026 | 0 oportunidades accionables",
        "executive_summary": "Se revisaron 2 activos y 0 fueron accionables.",
        "top_opportunities": [],
        "no_trade_notes": ["BTC — NO_TRADE: sin ventaja neta suficiente"],
        "data_health_note": "Calidad de datos valida.",
        "disclaimer": "No es asesoria financiera.",
    }
    return {**base, **overrides}


class FakeClient:
    def __init__(self, content: str, *, model: str = "fake-model") -> None:
        self.content = content
        self.model = model
        self.calls: list[list[dict[str, str]]] = []

    def chat_json(self, messages: list[dict[str, str]]) -> LLMResponse:
        self.calls.append(messages)
        return LLMResponse(
            text=self.content,
            model=self.model,
            input_tokens=100,
            output_tokens=50,
        )


def test_payload_is_deterministic_and_carries_display_values() -> None:
    batch = _batch()
    payload = build_payload(batch, _decisions(batch), _paper(), generated_at=NOW)

    assert payload["summary"]["actionable"] == 1
    assert payload["actionable_count"] == 1
    btc = payload["signals"][0]
    assert btc["costs"]["total_bps"] == pytest.approx(22.0)
    assert btc["costs"]["total_rate"] == pytest.approx(0.0022)
    assert btc["probability_net_positive_pct"] == pytest.approx(57.0)
    assert payload["paper"]["mode"] == "shadow"


def test_deterministic_explanation_describes_no_trade_and_trade() -> None:
    batch = _batch()
    payload = build_payload(batch, _decisions(batch), _paper(), generated_at=NOW)
    body = build_deterministic(payload, generated_at=NOW)

    assert "1 oportunidad accionable" in body["subject"]
    assert "Accionables tras costos: 1" in body["executive_summary"]
    assert [item["asset"] for item in body["top_opportunities"]] == ["BTC"]
    assert any(note.startswith("ETH") for note in body["no_trade_notes"])


def test_guard_rejects_invented_numbers() -> None:
    payload = build_payload(_batch(), _decisions(_batch()), _paper(), generated_at=NOW)
    report = LLMReport.model_validate(_body(executive_summary="Probabilidad 0.99 de ganar."))

    reason = guard_explanation(report, payload, generated_at=NOW)

    assert reason == "cifras_inventadas:0.99"
    assert invented_numbers("ganancia de 12.5%", {"12.5"}) == []


def test_guard_rejects_forbidden_phrases_and_invented_assets() -> None:
    payload = build_payload(_batch(), _decisions(_batch()), _paper(), generated_at=NOW)

    forbidden = LLMReport.model_validate(_body(executive_summary="Retorno garantizado."))
    assert guard_explanation(forbidden, payload, generated_at=NOW) == "frase_prohibida"

    invented = LLMReport.model_validate(
        _body(top_opportunities=[{"asset": "SOL", "signal": "LONG_BIAS", "why": "x"}])
    )
    assert guard_explanation(invented, payload, generated_at=NOW) == "activo_inventado:SOL"


def test_explain_daily_without_llm_persists_deterministic(store: Store) -> None:
    batch = _batch()
    explanation = explain_daily(
        batch=batch,
        decisions=_decisions(batch),
        paper=_paper(),
        store=store,
        settings=Settings(),
        generated_at=NOW,
    )

    assert explanation.kind == "deterministic"
    assert explanation.fallback_reason == "llm_disabled"
    stored = store.latest_explanation()
    assert stored is not None
    assert stored["kind"] == "deterministic"
    assert stored["fallback_reason"] == "llm_disabled"


def test_explain_daily_uses_valid_llm_output(store: Store) -> None:
    batch = _batch()
    client = FakeClient(json.dumps(_body()))

    explanation = explain_daily(
        batch=batch,
        decisions=_decisions(batch),
        paper=_paper(),
        store=store,
        settings=_llm_settings(),
        generated_at=NOW,
        client=client,
    )

    assert explanation.kind == "llm"
    assert explanation.fallback_reason is None
    assert explanation.model == "fake-model"
    assert explanation.input_tokens == 100
    assert explanation.cost_usd > 0
    assert len(client.calls) == 1
    assert store.latest_explanation()["kind"] == "llm"


def test_explain_daily_falls_back_on_invalid_schema_and_numbers(store: Store) -> None:
    batch = _batch()
    decisions = _decisions(batch)

    bad_schema = explain_daily(
        batch=batch,
        decisions=decisions,
        paper=_paper(),
        store=store,
        settings=_llm_settings(),
        generated_at=NOW,
        client=FakeClient("no es json"),
    )
    assert bad_schema.kind == "deterministic"
    assert bad_schema.fallback_reason == "invalid_schema"
    assert bad_schema.cost_usd > 0  # se pago la llamada: cuenta para el presupuesto

    bad_numbers = explain_daily(
        batch=batch,
        decisions=decisions,
        paper=_paper(),
        store=store,
        settings=_llm_settings(),
        generated_at=NOW,
        client=FakeClient(json.dumps(_body(executive_summary="Gane 0.99 y listo."))),
    )
    assert bad_numbers.kind == "deterministic"
    assert bad_numbers.fallback_reason == "cifras_inventadas:0.99"


def test_budget_blocks_the_call(store: Store) -> None:
    batch = _batch()
    client = FakeClient(json.dumps(_body()))

    explanation = explain_daily(
        batch=batch,
        decisions=_decisions(batch),
        paper=_paper(),
        store=store,
        settings=_llm_settings(llm_monthly_budget_usd=0.0),
        generated_at=NOW,
        client=client,
    )

    assert explanation.kind == "deterministic"
    assert explanation.fallback_reason == "budget_exceeded"
    assert client.calls == []


def test_llm_spend_counts_failed_calls(store: Store) -> None:
    store.save_explanation(
        generated_at=NOW,
        kind="deterministic",
        provider="rules",
        model="deterministic",
        prompt_version="v1",
        subject="s",
        body={},
        raw_response=None,
        valid=True,
        fallback_reason="invalid_schema",
        cost_usd=0.25,
    )
    assert store.llm_spend_since(NOW.replace(day=1)) == pytest.approx(0.25)


def test_chat_client_sends_the_expected_request() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["json"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "fake-model",
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            },
        )

    config = LLMConfig(base_url="https://fake.local/v1", api_key="secret", model="fake-model")
    client = ChatClient(config, transport=httpx.MockTransport(handler))

    response = client.chat_json([{"role": "user", "content": "hola"}])

    assert response.text == "{}"
    assert (response.input_tokens, response.output_tokens) == (7, 3)
    assert captured["url"] == "https://fake.local/v1/chat/completions"
    assert captured["auth"] == "Bearer secret"
    assert captured["json"]["response_format"] == {"type": "json_object"}


def test_chat_client_raises_on_http_error() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    client = ChatClient(
        LLMConfig(base_url="https://fake.local/v1", api_key="secret", model="fake-model"),
        transport=transport,
    )
    with pytest.raises(LLMError):
        client.chat_json([{"role": "user", "content": "hola"}])


def test_telegram_is_optional_and_sends_when_configured() -> None:
    disabled = Settings()
    assert deliver("s", {"executive_summary": "x"}, disabled) is False

    missing = Settings(telegram_enabled=True)
    assert deliver("s", {"executive_summary": "x"}, missing) is False

    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    enabled = Settings(
        telegram_enabled=True,
        telegram_bot_token="token",
        telegram_chat_id="42",
    )
    assert deliver(
        "Asunto",
        {"executive_summary": "resumen", "top_opportunities": [], "disclaimer": "aviso"},
        enabled,
        transport=httpx.MockTransport(handler),
    )
    assert calls and calls[0]["chat_id"] == "42"
    assert "Asunto" in calls[0]["text"]
