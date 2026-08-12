---
name: testing
description: Use when a Codentum worker must create acceptance tests, run targeted verification, interpret failures, or produce test evidence for a WorkPacket.
---

# Testing Skill

Tie every test to an explicit acceptance condition. A test run is evidence only when it can be rerun and linked to the packet.

Before writing tests:
- Read the WorkPacket acceptance predicate and changed paths.
- Prefer existing test style and fixtures.
- Add the narrowest test that would fail for the observed bug or missing behavior.

When running tests:
- Start with targeted tests for the changed module.
- Escalate to package or full offline verification only when the change touches shared behavior.
- Capture command, result, and notable failure lines in evidence or the final report.

Fail closed when validation cannot run. Report the missing dependency or environment condition instead of marking the work complete.
