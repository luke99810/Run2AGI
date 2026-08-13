---
name: delivery
description: Use when a Codentum integrator must choose an artifact shape, run build commands, verify a clean install or smoke check, and record delivery evidence for Agent-produced software.
---

# Delivery Skill

Use this skill after integration evidence exists and the task needs a distributable artifact or a delivery blocker.

Select the smallest artifact shape that matches the project:
- Python library: prefer `python -m build` and a wheel.
- Python application: prefer a source package or documented runner before PyInstaller.
- Web application: prefer package metadata, a build command, and static output or container notes.

Run build steps with `run_build`, not `run_tests`. A build failure is different from a failing test: report `failure_type=build`, include the command, log tail, and dependency manifest state. Run tests or smoke checks with `run_tests` only after the artifact or build output exists.

Do not install dependencies into the worker environment with `pip install`, `npm install`, `pnpm add`, `poetry add`, `uv sync`, or similar commands. Dependency changes must be written to a project manifest such as `pyproject.toml`, `requirements.txt`, or `package.json`, then verified by an isolated build or reported as a blocker when that isolated environment is unavailable.

For clean-environment verification, evidence must come from outside the build tree: a fresh venv, temporary directory, container, or clearly marked blocker if none is available. Importing from the current source directory is not delivery proof.

Write delivery evidence under `evidence/delivery/` or `.codentum/delivery/`. Never claim release, deployment, or production success without explicit artifact path, build command, smoke command, and verification result.
