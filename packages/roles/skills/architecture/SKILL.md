---
name: architecture
description: Use when a Codentum worker must define module boundaries, contracts, ADRs, ownership surfaces, or architectural tradeoffs before implementation.
---

# Architecture Skill

Protect system boundaries before code is written.

Start by locating the existing owner:
- Contracts and generated types belong to the contracts boundary.
- Runtime behavior belongs to harness, engine, delivery, desktop, or control-plane according to `boundaries.yaml`.
- Prefer extending the existing module seam over creating a parallel path.

When proposing a change:
- State the invariant being protected.
- Record the smallest contract or ADR change needed.
- Avoid widening read/write permissions unless a role genuinely needs them.

Fail closed when a requirement crosses ownership boundaries without an ADR or explicit handoff.
