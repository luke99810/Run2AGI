"""Deterministic pricing helpers for ModelGateway implementations."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "MissingModelPricingError",
    "TokenPricing",
]


class MissingModelPricingError(ValueError):
    """A model was invoked without auditable pricing metadata."""


@dataclass(frozen=True, slots=True)
class TokenPricing:
    """Per-token price table normalized to CNY per one million tokens."""

    input_per_million_cny: float
    output_per_million_cny: float
    cached_input_per_million_cny: float | None = None

    def __post_init__(self) -> None:
        values = (
            self.input_per_million_cny,
            self.output_per_million_cny,
            self.cached_input_per_million_cny,
        )
        if any(value is not None and value < 0 for value in values):
            raise ValueError("token pricing values must be non-negative")

    def cost_cny(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
    ) -> float:
        """Calculate cost from measured token usage."""

        if input_tokens < 0 or output_tokens < 0 or cached_input_tokens < 0:
            raise ValueError("token counts must be non-negative")

        cached_tokens = min(cached_input_tokens, input_tokens)
        billable_input_tokens = input_tokens - cached_tokens
        cached_rate = (
            self.input_per_million_cny
            if self.cached_input_per_million_cny is None
            else self.cached_input_per_million_cny
        )
        return (
            billable_input_tokens * self.input_per_million_cny
            + cached_tokens * cached_rate
            + output_tokens * self.output_per_million_cny
        ) / 1_000_000
