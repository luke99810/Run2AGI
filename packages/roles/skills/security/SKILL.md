---
name: security
description: Use when a Codentum worker must audit permissions, secrets, command execution, network access, prompt injection risk, or RoleSpec/tool-surface boundaries.
---

# Security Skill

Treat security evidence as a gate, not a suggestion.

Check:
- RoleSpec reads, writes, tools, invisible paths, and model policy.
- Secret handling in files, logs, environment variables, and test fixtures.
- Command execution surfaces, shell usage, network access, and path traversal.
- Prompt or context injection paths that could alter tool behavior.

A security review must identify the protected invariant and the evidence checked. If the evidence is missing, fail closed.
