"""Shared safety policy for ModelGateway implementations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from codentum_contracts.state import ModelId, ModelRouting, RoleId, RoleSpec

__all__ = [
    "ModelGatewayPolicy",
    "ModelIsolationError",
]


class ModelIsolationError(ValueError):
    """A role attempted to open a model session that violates isolation policy."""


@dataclass(frozen=True, slots=True)
class ModelGatewayPolicy:
    """Role-level model isolation policy enforced before a session is opened."""

    role_models: Mapping[RoleId, ModelId] = field(default_factory=dict)
    must_differ_from: Mapping[RoleId, Sequence[RoleId]] = field(default_factory=dict)
    compare_families: bool = False

    @classmethod
    def from_role_specs(
        cls,
        role_specs: Sequence[RoleSpec],
        *,
        compare_families: bool = False,
    ) -> ModelGatewayPolicy:
        role_models: dict[RoleId, ModelId] = {}
        must_differ_from: dict[RoleId, tuple[RoleId, ...]] = {}

        for role_spec in role_specs:
            policy = role_spec.modelPolicy
            if policy is None:
                continue
            if policy.defaultModel is not None:
                role_models[role_spec.id] = policy.defaultModel
            if policy.mustDifferFrom:
                must_differ_from[role_spec.id] = tuple(policy.mustDifferFrom)

        return cls(
            role_models=role_models,
            must_differ_from=must_differ_from,
            compare_families=compare_families,
        )

    def validate_open(self, role: RoleId, routing: ModelRouting) -> None:
        """Reject sessions that reuse a forbidden peer role's model."""

        model = routing.model
        for peer_role in self.must_differ_from.get(role, ()):
            peer_model = self.role_models.get(peer_role)
            if peer_model is None:
                continue
            if self._same_model(model, peer_model):
                comparison = "family" if self.compare_families else "model"
                raise ModelIsolationError(
                    f"{role} model {model!r} must differ from {peer_role} "
                    f"{comparison} {peer_model!r}"
                )

    def _same_model(self, left: ModelId, right: ModelId) -> bool:
        if self.compare_families:
            return _model_family(left) == _model_family(right)
        return bool(left == right)


def _model_family(model: ModelId) -> str:
    raw = str(model).strip().lower()
    if "/" in raw:
        raw = raw.split("/", 1)[0]
    if "-" in raw:
        raw = raw.split("-", 1)[0]
    if "." in raw:
        raw = raw.split(".", 1)[0]
    return raw
