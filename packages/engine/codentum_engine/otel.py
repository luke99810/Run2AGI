"""OpenTelemetry GenAI 可观测导出 —— 自研 Schema，遵循 OTel GenAI 语义规范。

★ 零第三方依赖：不 import OpenTelemetry SDK，只产出遵循 OTel GenAI
  语义约定（`gen_ai.*` 属性）的 span 数据，并可序列化为 OTLP/JSON
  （`resourceSpans → scopeSpans → spans`）。

★ 为什么自研而非直接依赖 OTel SDK：
  - 引擎的承诺是「零第三方依赖、可离线复现」，OTel SDK 会引入采集/采样/传输。
  - 这里只需要「产出 OTel 兼容的轨迹」，不需要 OTel 的运行时。
  - 序列化为 OTLP/JSON 后，任何 OTel Collector 都能直接接收，迁移成本为零。

★ 覆盖的 OTel GenAI 语义约定（GenAI semantic conventions）：
  - `gen_ai.operation.name` —— chat / execute_tool
  - `gen_ai.system` · `gen_ai.request.model` · `gen_ai.response.model`
  - `gen_ai.usage.input_tokens` · `gen_ai.usage.output_tokens`
  - `gen_ai.tool.name` · `gen_ai.tool.call.id`
  - `error.type` —— 失败时的错误类型

★ 确定性：trace_id / span_id 由内容摘要派生（hashlib），同输入同 ID，
  replay 时可复现同一条轨迹 —— 与 MemoryIndex 的确定性承诺同一原则。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Literal

from codentum_contracts.interfaces import ModelResponse

__all__ = [
    "GenAiSpan",
    "SpanStatus",
    "genai_spans_from_model_response",
    "new_span_id",
    "new_trace_id",
    "to_otlp_json",
]

_GENAI_SCOPE = "codentum"
_GENAI_SCOPE_VERSION = "0.1.0"

SpanStatus = Literal["UNSET", "OK", "ERROR"]
SpanKind = Literal["INTERNAL", "CLIENT", "SERVER", "PRODUCER", "CONSUMER"]

_KIND_CODE: dict[SpanKind, int] = {
    "INTERNAL": 1,
    "CLIENT": 3,
    "SERVER": 2,
    "PRODUCER": 4,
    "CONSUMER": 5,
}
_STATUS_CODE: dict[SpanStatus, int] = {"UNSET": 0, "OK": 1, "ERROR": 2}


@dataclass(frozen=True, slots=True)
class GenAiSpan:
    """一条遵循 OTel GenAI 语义约定的 span。

    ★ `attributes` 的键一律是 `gen_ai.*`（语义约定），
      额外信息（模型、角色、成本）也用语义约定键或 `codentum.*` 前缀。
    """

    trace_id: str
    span_id: str
    parent_span_id: str
    name: str
    kind: SpanKind = "INTERNAL"
    start_time_unix_nano: int = 0
    end_time_unix_nano: int = 0
    attributes: dict[str, Any] = field(default_factory=dict)
    status: SpanStatus = "UNSET"
    status_message: str = ""


def new_trace_id(seed: str) -> str:
    """确定性 trace_id（32 位十六进制），由 seed 摘要派生。"""
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return digest[:32]


def new_span_id(seed: str) -> str:
    """确定性 span_id（16 位十六进制），由 seed 摘要派生。"""
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return digest[:16]


def genai_spans_from_model_response(
    *,
    trace_seed: str,
    model: str,
    system: str,
    role: str,
    response: ModelResponse,
    start_time_unix_nano: int = 0,
    end_time_unix_nano: int = 0,
    error_type: str | None = None,
    trace_id: str | None = None,
) -> tuple[GenAiSpan, ...]:
    """把一次模型调用（ModelResponse）转成 OTel GenAI spans。

    返回一条 `chat` span（父），外加每条 tool_call 一条 `execute_tool` span。
    顺序稳定：先 chat，再按 tool_call 顺序展开。

    ★ `trace_id` 可以显式传入，否则由 `trace_seed` 派生。

      这个参数是接线时才发现要加的：一次 packet 执行是**多轮**模型调用，
      而 trace_id 和 chat 的 span_id 原本都从同一个 seed 派生 —— 于是
      「每轮换 seed」会把一次执行拆成 N 条互不相干的 trace，
      「每轮同 seed」又会让 N 个 chat span 撞同一个 span_id。
      两种都不是一条能看的 trace。调用方要能分开指定这两者。
    """
    trace_id = trace_id or new_trace_id(trace_seed)
    chat_span_id = new_span_id(f"{trace_seed}:chat")

    chat_attrs: dict[str, Any] = {
        "gen_ai.operation.name": "chat",
        "gen_ai.system": system,
        "gen_ai.request.model": model,
        "gen_ai.response.model": model,
        "gen_ai.usage.input_tokens": response.usage.input_tokens,
        "gen_ai.usage.output_tokens": response.usage.output_tokens,
        "codentum.role": role,
        "codentum.cost_cny": response.usage.cost_cny,
        "codentum.cached_input_tokens": response.usage.cached_input_tokens,
    }
    chat_status: SpanStatus = "ERROR" if error_type else "OK"
    chat_status_message = error_type or ""

    chat = GenAiSpan(
        trace_id=trace_id,
        span_id=chat_span_id,
        parent_span_id="",
        name=f"chat {model}",
        kind="CLIENT",
        start_time_unix_nano=start_time_unix_nano,
        end_time_unix_nano=end_time_unix_nano,
        attributes=chat_attrs,
        status=chat_status,
        status_message=chat_status_message,
    )

    spans: list[GenAiSpan] = [chat]
    for index, tool_call in enumerate(response.tool_calls):
        spans.append(
            GenAiSpan(
                trace_id=trace_id,
                span_id=new_span_id(f"{trace_seed}:tool:{index}:{tool_call.id}"),
                parent_span_id=chat_span_id,
                name=f"execute_tool {tool_call.name}",
                kind="INTERNAL",
                attributes={
                    "gen_ai.operation.name": "execute_tool",
                    "gen_ai.system": system,
                    "gen_ai.tool.name": tool_call.name,
                    "gen_ai.tool.call.id": tool_call.id,
                },
                status="OK",
            )
        )
    return tuple(spans)


def to_otlp_json(
    spans: tuple[GenAiSpan, ...],
    *,
    service_name: str = "codentum",
) -> dict[str, Any]:
    """把 spans 序列化为 OTLP/JSON（resourceSpans → scopeSpans → spans）。

    ★ 不产 Resource/Scope 之外的噪声；属性值按 OTLP AnyValue 编码
      （int → intValue，str → stringValue，float → doubleValue）。
    """
    otlp_spans: list[dict[str, Any]] = []
    for span in spans:
        otlp_spans.append(
            {
                "traceId": span.trace_id,
                "spanId": span.span_id,
                "parentSpanId": span.parent_span_id,
                "name": span.name,
                "kind": _KIND_CODE[span.kind],
                "startTimeUnixNano": str(span.start_time_unix_nano),
                "endTimeUnixNano": str(span.end_time_unix_nano),
                "attributes": [
                    {"key": key, "value": _any_value(value)}
                    for key, value in sorted(span.attributes.items())
                ],
                "status": {
                    "code": _STATUS_CODE[span.status],
                    "message": span.status_message,
                },
            }
        )
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": service_name}}
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": _GENAI_SCOPE, "version": _GENAI_SCOPE_VERSION},
                        "spans": otlp_spans,
                    }
                ],
            }
        ]
    }


def _any_value(value: Any) -> dict[str, Any]:
    """OTLP AnyValue 编码：int → intValue，float → doubleValue，其余 → stringValue。"""
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": value}
    if isinstance(value, float):
        return {"doubleValue": value}
    return {"stringValue": str(value)}
