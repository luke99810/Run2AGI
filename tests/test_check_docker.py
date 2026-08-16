from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

import scripts.check_docker as docker_check


def test_purpose_comment_must_explain_container_role() -> None:
    findings = docker_check.check_purpose_comment(
        Path("Dockerfile"),
        ["# generic comment", "FROM python:3.11-slim"],
    )

    assert findings
    assert findings[0].rule == "用途说明"


def test_pinned_base_rejects_latest_and_implicit_latest() -> None:
    latest = docker_check.check_pinned_base(Path("Dockerfile"), ["FROM node:latest"])
    implicit = docker_check.check_pinned_base(Path("Dockerfile"), ["FROM python"])

    assert any(item.rule == "版本 pin" for item in latest)
    assert any(item.rule == "版本 pin" for item in implicit)


def test_baked_credentials_detects_underscored_provider_keys() -> None:
    findings = docker_check.check_no_baked_credentials(
        Path("Dockerfile"),
        ["ENV DASHSCOPE_API_KEY=short"],
    )

    assert findings
    assert findings[0].rule == "凭证烘焙"


def test_runtime_arg_without_default_is_allowed() -> None:
    findings = docker_check.check_no_baked_credentials(
        Path("Dockerfile"),
        ["ARG DASHSCOPE_API_KEY"],
    )

    assert findings == []


def test_cold_start_rejects_copying_entire_build_context() -> None:
    findings = docker_check.check_cold_start_is_from_zero(
        Path("docker/cold-start/Dockerfile"),
        ["COPY . /app"],
    )

    assert findings
    assert findings[0].rule == "冷启动从零"


def test_entrypoint_scripts_must_be_lf_and_have_shebang(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    docker = tmp_path / "docker" / "cold-start"
    docker.mkdir(parents=True)
    (docker / "entrypoint.sh").write_bytes(b"echo bad\r\n")
    monkeypatch.setattr(docker_check, "REPO", tmp_path)
    monkeypatch.setattr(docker_check, "DOCKER", tmp_path / "docker")

    findings = docker_check.check_entrypoint_scripts_are_lf()

    assert any("CRLF" in item.detail for item in findings)
    assert any("shebang" in item.detail for item in findings)
