"""Smoke-test a concrete ModelGateway without exposing provider secrets."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Literal, cast

from codentum_contracts import Effort, ModelGateway, ModelRouting, RoleId
from codentum_contracts.interfaces import ModelMessage, ModelRequest

from codentum_harness.runtime import ModelGatewayConfig, TokenPricingConfig, build_model_gateway

__all__ = [
    "DEFAULT_SMOKE_PROMPT",
    "SmokeResult",
    "main",
    "run_model_gateway_smoke",
]

DEFAULT_SMOKE_PROMPT = "Reply with exactly: codentum-smoke-ok"

ProviderKind = Literal["bailian", "openai-compatible", "anthropic"]

ROLE_CHOICES: tuple[RoleId, ...] = (
    "intake",
    "architect",
    "planner",
    "qa",
    "coder",
    "helper",
    "reviewer",
    "integrator",
    "manager",
    "evolver",
    "guardian",
)

EFFORT_CHOICES: tuple[Effort, ...] = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True, slots=True)
class SmokeResult:
    """Safe summary of one provider smoke invocation."""

    provider: str
    session_id: str
    model: str
    role: str
    stop_reason: str
    usage: dict[str, object]
    ledger_total_cny: float
    text_preview: str

    def to_json_dict(self) -> dict[str, object]:
        return asdict(self)


async def run_model_gateway_smoke(
    gateway: ModelGateway,
    *,
    provider: str,
    role: RoleId,
    routing: ModelRouting,
    grant_cny: float,
    prompt: str = DEFAULT_SMOKE_PROMPT,
) -> SmokeResult:
    """Open a session, invoke one tiny prompt, and return a non-secret summary."""

    session = await gateway.open(role, routing, grant_cny)
    try:
        response = await session.invoke(
            ModelRequest(
                system=(
                    "You are running a Codentum provider smoke test. "
                    "Do not include secrets or environment details in the response."
                ),
                messages=(ModelMessage(role="user", content=prompt),),
                effort=routing.effort,
            )
        )
        session_id = session.session_id
        model = session.model
        session_role = session.role
    finally:
        await session.close()

    ledger = await gateway.ledger()
    return SmokeResult(
        provider=provider,
        session_id=session_id,
        model=model,
        role=session_role,
        stop_reason=response.stop_reason,
        usage=asdict(response.usage),
        ledger_total_cny=ledger.total_cny,
        text_preview=response.text[:200],
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        config = _config_from_args(args)
        provider = cast(ProviderKind, args.provider)
        routing = ModelRouting(model=args.model, effort=cast(Effort, args.effort))
        result = asyncio.run(
            run_model_gateway_smoke(
                build_model_gateway(config),
                provider=provider,
                role=cast(RoleId, args.role),
                routing=routing,
                grant_cny=args.grant_cny,
                prompt=args.prompt,
            )
        )
    except Exception as exc:
        sys.stderr.write(f"model gateway smoke failed: {exc}\n")
        return 2

    sys.stdout.write(json.dumps(result.to_json_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one safe Codentum ModelGateway smoke invocation.",
    )
    parser.add_argument(
        "--provider",
        choices=("bailian", "openai-compatible", "anthropic"),
        default="bailian",
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--role", choices=ROLE_CHOICES, default="coder")
    parser.add_argument("--effort", choices=EFFORT_CHOICES, default="low")
    parser.add_argument("--grant-cny", type=float, default=0.05)
    parser.add_argument("--base-url")
    parser.add_argument("--api-key-env")
    parser.add_argument("--provider-timeout-seconds", type=float, default=60.0)
    parser.add_argument("--input-price-cny-per-million", type=float)
    parser.add_argument("--output-price-cny-per-million", type=float)
    parser.add_argument("--cached-input-price-cny-per-million", type=float)
    parser.add_argument(
        "--allow-unknown-pricing",
        action="store_true",
        help="Permit smoke calls without pricing; usage.cost_cny will be 0.",
    )
    parser.add_argument("--prompt", default=DEFAULT_SMOKE_PROMPT)
    return parser


def _config_from_args(args: argparse.Namespace) -> ModelGatewayConfig:
    pricing, require_pricing = _pricing_from_args(args)
    provider = cast(ProviderKind, args.provider)
    config = ModelGatewayConfig(
        kind=provider,
        pricing=pricing,
        api_key_env=args.api_key_env,
        base_url=args.base_url,
        require_pricing=require_pricing,
        provider_timeout_seconds=args.provider_timeout_seconds,
    )
    if provider == "bailian":
        return ModelGatewayConfig.bailian(
            pricing=pricing,
        api_key_env=args.api_key_env,
        base_url=args.base_url,
        require_pricing=require_pricing,
        provider_timeout_seconds=args.provider_timeout_seconds,
    )
    return config


def _pricing_from_args(args: argparse.Namespace) -> tuple[dict[str, TokenPricingConfig], bool]:
    if args.allow_unknown_pricing:
        return {}, False
    if args.input_price_cny_per_million is None or args.output_price_cny_per_million is None:
        raise ValueError(
            "pricing is required unless --allow-unknown-pricing is set "
            "(pass --input-price-cny-per-million and --output-price-cny-per-million)"
        )
    return (
        {
            args.model: TokenPricingConfig(
                input_per_million_cny=args.input_price_cny_per_million,
                output_per_million_cny=args.output_price_cny_per_million,
                cached_input_per_million_cny=args.cached_input_price_cny_per_million,
            )
        },
        True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
