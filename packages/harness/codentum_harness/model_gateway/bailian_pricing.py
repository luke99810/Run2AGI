"""Audited Bailian/DashScope model pricing used for CNY attribution."""

from __future__ import annotations

from types import MappingProxyType

from codentum_contracts.state import ModelId

from .pricing import TokenPricing

__all__ = [
    "BAILIAN_PRICING_COLLECTED_AT",
    "BAILIAN_PRICING_SOURCE_URL",
    "audited_bailian_pricing",
]

BAILIAN_PRICING_SOURCE_URL = "https://help.aliyun.com/zh/model-studio/model-pricing"
BAILIAN_PRICING_COLLECTED_AT = "2026-08-12"

_QWEN_PLUS_128K = TokenPricing(
    input_per_million_cny=0.8,
    output_per_million_cny=2.0,
    max_input_tokens=128_000,
)
_QWEN_PLUS_FLAT = TokenPricing(
    input_per_million_cny=0.8,
    output_per_million_cny=2.0,
)
_QWEN_CODER_PLUS = TokenPricing(
    input_per_million_cny=3.5,
    output_per_million_cny=7.0,
)
_QWEN36_PLUS_256K = TokenPricing(
    input_per_million_cny=2.0,
    output_per_million_cny=12.0,
    max_input_tokens=256_000,
)

# Values come from Aliyun Bailian's official model pricing page. Only models that
# Codentum actually routes to or uses in AgentTeams smoke are listed here; missing
# models still fail closed through ModelGateway.require_pricing.
_PRICING: dict[ModelId, TokenPricing] = {
    "qwen-coder-plus": _QWEN_CODER_PLUS,
    "qwen-coder-plus-1106": _QWEN_CODER_PLUS,
    "qwen-plus": _QWEN_PLUS_128K,
    "qwen-plus-latest": _QWEN_PLUS_128K,
    "qwen-plus-2025-11-05": _QWEN_PLUS_128K,
    "qwen-plus-2025-07-14": _QWEN_PLUS_FLAT,
    "qwen3.6-plus": _QWEN36_PLUS_256K,
    "qwen3.6-plus-2026-04-02": _QWEN36_PLUS_256K,
}


def audited_bailian_pricing() -> MappingProxyType[ModelId, TokenPricing]:
    """Return the audited Bailian price table.

    The mapping is read-only so callers can safely pass it through runtime
    construction without accidental mutation.
    """

    return MappingProxyType(_PRICING)
