---
name: debugging
description: Use when a Codentum worker must diagnose a failing test, blocked worker, runtime error, missing evidence, sidecar issue, or integration mismatch.
---

# Debugging Skill

Find the first failing fact, not the most convenient explanation.

Debug in layers:
- Reproduce the failure with the smallest command or state read.
- Use `run_build` for build/package failures and `run_tests` for test failures; keep the failure type separate.
- Locate whether the break is source, generated state, runtime environment, or UI projection.
- Compare expected evidence with actual files under `.codentum/`.

When proposing a fix:
- Name the broken handoff.
- Keep the change close to the owner module.
- Add a regression test that would have failed before the fix.

If the failure depends on local credentials, an unavailable service, or dependency installation that would pollute the worker environment, report that as a blocker instead of guessing or installing packages.
