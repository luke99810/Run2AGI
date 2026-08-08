"""Factory helpers for assembling local worker runtimes."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from codentum_contracts import ModelGateway, RoleSpec

from codentum_harness.model_gateway import (
    AnthropicGateway,
    BailianOpenAICompatibleGateway,
    ModelGatewayPolicy,
    OpenAICompatibleGateway,
    TokenPricing,
)
from codentum_harness.runner import CommandRunner, ModelGatewayRunner
from codentum_harness.worker import LocalWorkerRuntime, WorkerContextLoader, WorkerRunner

__all__ = [
    "LocalWorkerRuntimeConfig",
    "ModelGatewayConfig",
    "RunnerConfig",
    "TokenPricingConfig",
    "build_local_worker_runtime",
    "build_model_gateway",
    "build_runner",
]


@dataclass(frozen=True, slots=True)
class TokenPricingConfig:
    """Serializable price table normalized to USD per one million tokens."""

    input_per_million_usd: float
    output_per_million_usd: float
    cached_input_per_million_usd: float | None = None

    def to_pricing(self) -> TokenPricing:
        return TokenPricing(
            input_per_million_usd=self.input_per_million_usd,
            output_per_million_usd=self.output_per_million_usd,
            cached_input_per_million_usd=self.cached_input_per_million_usd,
        )


@dataclass(frozen=True, slots=True)
class ModelGatewayConfig:
    """Serializable ModelGateway provider selection.

    API keys are read from environment variables at construction time; key values
    should not be stored in this config.
    """

    kind: Literal["bailian", "openai-compatible", "anthropic"]
    pricing: Mapping[str, TokenPricingConfig] = field(default_factory=dict)
    api_key_env: str | None = None
    base_url: str | None = None
    require_pricing: bool = True
    compare_model_families: bool = False
    provider_timeout_seconds: float = 120.0

    @classmethod
    def bailian(
        cls,
        *,
        pricing: Mapping[str, TokenPricingConfig],
        api_key_env: str | None = None,
        base_url: str | None = None,
        require_pricing: bool = True,
        compare_model_families: bool = False,
    ) -> ModelGatewayConfig:
        return cls(
            kind="bailian",
            pricing=dict(pricing),
            api_key_env=api_key_env,
            base_url=base_url,
            require_pricing=require_pricing,
            compare_model_families=compare_model_families,
        )


@dataclass(frozen=True, slots=True)
class RunnerConfig:
    """Serializable runner selection for the local harness composition root."""

    kind: Literal["none", "command", "model_gateway"] = "none"
    command: Sequence[str] = ()
    gateway: ModelGatewayConfig | None = None
    timeout_seconds: float = 900.0
    env: Mapping[str, str] | None = None

    @classmethod
    def command_runner(
        cls,
        command: Sequence[str],
        *,
        timeout_seconds: float = 900.0,
        env: Mapping[str, str] | None = None,
    ) -> RunnerConfig:
        return cls(
            kind="command",
            command=tuple(command),
            timeout_seconds=timeout_seconds,
            env=None if env is None else dict(env),
        )

    @classmethod
    def model_gateway(
        cls,
        gateway: ModelGatewayConfig,
        *,
        timeout_seconds: float = 900.0,
    ) -> RunnerConfig:
        return cls(
            kind="model_gateway",
            gateway=gateway,
            timeout_seconds=timeout_seconds,
        )


@dataclass(frozen=True, slots=True)
class LocalWorkerRuntimeConfig:
    """Configuration for creating a local WorkerRuntime implementation."""

    repo_root: Path | str
    runner: RunnerConfig | None = None
    context_char_budget: int | None = None


def build_runner(
    config: RunnerConfig | None,
    *,
    role_specs: tuple[RoleSpec, ...] | None = None,
) -> WorkerRunner | None:
    """Create the WorkerRunner selected by config."""

    if config is None or config.kind == "none":
        return None
    if config.kind == "command":
        if not config.command:
            raise ValueError("command runner requires a non-empty command")
        if config.timeout_seconds <= 0:
            raise ValueError("command runner timeout_seconds must be positive")
        return CommandRunner(
            tuple(config.command),
            timeout_seconds=config.timeout_seconds,
            env=config.env,
        )
    if config.kind == "model_gateway":
        if config.gateway is None:
            raise ValueError("model_gateway runner requires gateway config")
        if config.timeout_seconds <= 0:
            raise ValueError("model_gateway runner timeout_seconds must be positive")
        return ModelGatewayRunner(
            build_model_gateway(config.gateway, role_specs=role_specs),
            timeout_seconds=config.timeout_seconds,
        )
    raise ValueError(f"unsupported runner kind: {config.kind}")


def build_model_gateway(
    config: ModelGatewayConfig,
    *,
    role_specs: tuple[RoleSpec, ...] | None = None,
) -> ModelGateway:
    """Create the ModelGateway selected by config."""

    if config.provider_timeout_seconds <= 0:
        raise ValueError("provider_timeout_seconds must be positive")

    pricing = {model: item.to_pricing() for model, item in config.pricing.items()}
    policy = ModelGatewayPolicy.from_role_specs(
        role_specs or (),
        compare_families=config.compare_model_families,
    )

    if config.kind == "bailian":
        api_key_envs = (
            (config.api_key_env,)
            if config.api_key_env is not None
            else BailianOpenAICompatibleGateway.API_KEY_ENVS
        )
        return BailianOpenAICompatibleGateway.from_sdk(
            api_key=_first_env(api_key_envs),
            base_url=config.base_url or BailianOpenAICompatibleGateway.DEFAULT_BASE_URL,
            pricing=pricing,
            policy=policy,
            require_pricing=config.require_pricing,
            timeout_seconds=config.provider_timeout_seconds,
        )

    if config.kind == "openai-compatible":
        if config.base_url is None:
            raise ValueError("openai-compatible gateway requires base_url")
        return OpenAICompatibleGateway.from_sdk(
            api_key=_first_env((config.api_key_env or "OPENAI_API_KEY",)),
            base_url=config.base_url,
            pricing=pricing,
            policy=policy,
            require_pricing=config.require_pricing,
            timeout_seconds=config.provider_timeout_seconds,
        )

    if config.kind == "anthropic":
        return AnthropicGateway.from_env(
            pricing=pricing,
            policy=policy,
            api_key_env=config.api_key_env or "ANTHROPIC_API_KEY",
            require_pricing=config.require_pricing,
            timeout_seconds=config.provider_timeout_seconds,
        )

    raise ValueError(f"unsupported model gateway kind: {config.kind}")


def build_local_worker_runtime(
    config: LocalWorkerRuntimeConfig,
    *,
    role_specs: tuple[RoleSpec, ...] | None = None,
    context_loader: WorkerContextLoader | None = None,
) -> LocalWorkerRuntime:
    """Assemble LocalWorkerRuntime without exposing harness internals to callers."""

    if config.context_char_budget is not None and config.context_char_budget <= 0:
        raise ValueError("context_char_budget must be positive")
    return LocalWorkerRuntime(
        repo_root=config.repo_root,
        runner=build_runner(config.runner, role_specs=role_specs),
        role_specs=role_specs,
        context_loader=context_loader,
        context_char_budget=config.context_char_budget,
    )


def _first_env(names: Sequence[str]) -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    raise RuntimeError(f"none of the API key environment variables are set: {', '.join(names)}")
