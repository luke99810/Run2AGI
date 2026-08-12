---
name: review
description: Use when a Codentum worker must review code, evidence, boundaries, RoleSpec permissions, or acceptance claims before a WorkPacket can be accepted.
---

# Review Skill

Lead with findings. Prefer concrete file, line, evidence, and gate references over broad commentary.

Check in this order:
- Contract or boundary drift.
- Missing or invalid evidence.
- Behavior regressions and untested paths.
- Permission widening, hidden context leakage, or RoleSpec/tool-surface mismatch.
- Cost, model, and runtime claims that are not backed by recorded usage.

A review passes only when there are no blocking findings and the evidence can be independently rechecked. If evidence is absent or unverifiable, return fail with the missing evidence listed.
