"""OTel GenAI 可观测导出 —— 自研 Schema 遵循 OTel GenAI 语义约定。"""

from __future__ import annotations

from codentum_contracts.interfaces import ModelResponse, ToolCall, Usage
from codentum_engine.otel import (
    genai_spans_from_model_response,
    new_span_id,
    new_trace_id,
    to_otlp_json,
)


def _response(*, tool_calls: tuple[ToolCall, ...] = ()) -> ModelResponse:
    return ModelResponse(
        text="已完成",
        tool_calls=tool_calls,
        stop_reason="end",
        usage=Usage(cost_cny=0.0123, input_tokens=120, output_tokens=40, cached_input_tokens=30),
    )


def test_genai_spans_follow_genai_semantic_conventions() -> None:
    spans = genai_spans_from_model_response(
        trace_seed="wp-abc123:attempt-1",
        model="qwen-plus-2025-07-14",
        system="dashscope",
        role="coder",
        response=_response(),
    )

    chat = spans[0]
    assert chat.name == "chat qwen-plus-2025-07-14"
    assert chat.kind == "CLIENT"
    assert chat.attributes["gen_ai.operation.name"] == "chat"
    assert chat.attributes["gen_ai.system"] == "dashscope"
    assert chat.attributes["gen_ai.request.model"] == "qwen-plus-2025-07-14"
    assert chat.attributes["gen_ai.usage.input_tokens"] == 120
    assert chat.attributes["gen_ai.usage.output_tokens"] == 40
    assert chat.attributes["codentum.role"] == "coder"
    assert chat.attributes["codentum.cost_cny"] == 0.0123
    assert chat.status == "OK"


def test_tool_calls_become_execute_tool_spans() -> None:
    spans = genai_spans_from_model_response(
        trace_seed="wp-abc123:attempt-1",
        model="qwen-plus-2025-07-14",
        system="dashscope",
        role="coder",
        response=_response(
            tool_calls=(
                ToolCall(id="call-1", name="read_file", input={"path": "a.py"}),
                ToolCall(id="call-2", name="write_file", input={"path": "b.py"}),
            )
        ),
    )

    assert len(spans) == 3  # 1 chat + 2 execute_tool
    chat, tool_a, tool_b = spans
    assert tool_a.name == "execute_tool read_file"
    assert tool_a.parent_span_id == chat.span_id
    assert tool_a.attributes["gen_ai.operation.name"] == "execute_tool"
    assert tool_a.attributes["gen_ai.tool.name"] == "read_file"
    assert tool_a.attributes["gen_ai.tool.call.id"] == "call-1"
    assert tool_b.attributes["gen_ai.tool.name"] == "write_file"


def test_trace_and_span_ids_are_deterministic() -> None:
    def ids() -> tuple[str, str]:
        spans = genai_spans_from_model_response(
            trace_seed="wp-abc123:attempt-1",
            model="qwen-plus",
            system="dashscope",
            role="coder",
            response=_response(),
        )
        return spans[0].trace_id, spans[0].span_id

    assert ids() == ids()
    assert new_trace_id("x") == new_trace_id("x")
    assert new_span_id("x") == new_span_id("x")
    assert new_trace_id("x") != new_trace_id("y")


def test_error_sets_span_status_to_error() -> None:
    spans = genai_spans_from_model_response(
        trace_seed="wp-abc123:attempt-1",
        model="qwen-plus",
        system="dashscope",
        role="coder",
        response=_response(),
        error_type="budget_exhausted",
    )
    assert spans[0].status == "ERROR"
    assert spans[0].status_message == "budget_exhausted"


def test_otlp_json_has_resource_scope_span_structure() -> None:
    spans = genai_spans_from_model_response(
        trace_seed="wp-abc123:attempt-1",
        model="qwen-plus",
        system="dashscope",
        role="coder",
        response=_response(tool_calls=(ToolCall(id="c1", name="read_file", input={}),)),
    )
    otlp = to_otlp_json(spans, service_name="codentum")

    assert otlp["resourceSpans"][0]["resource"]["attributes"][0] == {
        "key": "service.name",
        "value": {"stringValue": "codentum"},
    }
    scope_spans = otlp["resourceSpans"][0]["scopeSpans"][0]
    assert scope_spans["scope"]["name"] == "codentum"
    exported = scope_spans["spans"]
    assert len(exported) == 2
    # chat span 的属性必须是 OTel 语义约定键
    attr_keys = {a["key"] for a in exported[0]["attributes"]}
    assert "gen_ai.operation.name" in attr_keys
    assert "gen_ai.request.model" in attr_keys
    # traceId/spanId 是十六进制字符串
    assert len(exported[0]["traceId"]) == 32
    assert len(exported[0]["spanId"]) == 16
