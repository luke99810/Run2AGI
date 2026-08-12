---
name: backend
description: Use when a Codentum worker must implement engine, harness, delivery, control-plane, contracts-adjacent, filesystem, or Python backend behavior.
---

# Backend Skill

Implement behavior in the owning backend module without drifting contracts.

Before editing:
- Identify whether the change belongs to engine, harness, delivery, control-plane, roles, or contracts.
- Read the nearest tests and existing error semantics.
- Check whether the behavior is runtime state, evidence, protocol, or pure helper logic.

While editing:
- Keep public contracts stable unless an ADR or owner handoff requires a change.
- Prefer structured parsing and typed models over ad hoc string handling.
- Write fail-closed errors when missing evidence would otherwise look successful.

Validate with targeted Python tests first, then broaden to `make verify-offline` when shared behavior changed.
