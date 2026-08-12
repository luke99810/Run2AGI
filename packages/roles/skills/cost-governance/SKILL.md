---
name: cost-governance
description: Use when a Codentum worker must estimate token/model cost, enforce budget limits, explain CNY attribution, or decide degradation and routing.
---

# Cost Governance Skill

Make cost visible before it becomes a surprise.

Check:
- Packet budget, global budget, model route, effort, and degradation chain.
- Whether the cost claim is backed by usage evidence.
- Whether a cheaper context mode or model route preserves the required evidence.

When budget is insufficient:
- Prefer deterministic degradation from full text to summary to reference.
- Record what was omitted and why.
- Fail closed when required context cannot fit.

Do not invent CNY totals when pricing evidence is unknown.
