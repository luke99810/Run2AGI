---
name: planning
description: Use when a Codentum worker must split a brief into WorkPackets, estimate dependencies, assign roles, define acceptance gates, or rebalance execution order.
---

# Planning Skill

Turn approved scope into executable WorkPackets.

Plan packets so each one has:
- One primary owner role.
- Explicit `ownsPaths`, `readsPaths`, dependencies, budget, and acceptance predicate.
- A review path that does not let the implementer accept their own work.

Prefer small packets that can fail independently. Do not hide unknowns in a large implementation packet.

When a dependency is uncertain, make it explicit in the graph and mark the packet blocked until the upstream evidence exists.
