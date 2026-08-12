---
name: integration
description: Use when a Codentum worker must merge accepted work, run green-line verification, inspect cross-module regressions, or produce integration evidence.
---

# Integration Skill

Integrate only work that has reviewable evidence.

Before merging:
- Confirm each input change has an accepted review or explicit gate evidence.
- Check dependency order and path ownership.
- Prefer fast targeted green-line checks, then broaden when shared contracts or UI state changed.

While producing evidence:
- Record the exact commands and state files inspected.
- Separate merge conflicts from validation failures.
- Do not report success when dispatch, result collection, or verification is still missing.
