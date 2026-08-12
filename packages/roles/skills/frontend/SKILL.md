---
name: frontend
description: Use when a Codentum worker must implement or review desktop/Web UI work, React components, TypeScript renderer logic, CSS layout, or frontend tests inside the project.
---

# Frontend Skill

Use the existing desktop structure first. Prefer changing the view, input, panel, renderer, or data module that already owns the behavior.

Before editing:
- Identify whether the task is UI behavior, state projection, styles, or tests.
- Read the nearest existing component and its tests before adding a new component.
- Keep the source of truth in project state or RoleSpec projections; do not add static UI facts when a state field exists.

While editing:
- Preserve the current workbench style and interaction patterns.
- Use existing icons, panels, and task-library helpers.
- Keep text compact inside controls and cards.
- Do not claim runtime availability unless the state projection or engine capability proves it.

Validate with the narrowest relevant checks first:
- `npm run typecheck`
- `npm test -- <target test file>`
- Screenshot or smoke checks only when visual layout changed.
