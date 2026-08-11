from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_secret_scan() -> ModuleType:
    script = Path(__file__).resolve().parents[1] / "scripts" / "secret_scan.py"
    spec = importlib.util.spec_from_file_location("codentum_secret_scan_script", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_script_secret_scan_allows_explicit_sk_test_placeholder() -> None:
    scanner = _load_secret_scan()
    scanner.findings.clear()

    scanner.scan_text(
        'monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-not-a-real-key-for-tests")',
        "test",
    )

    assert scanner.findings == []


def test_script_secret_scan_still_flags_openai_like_secret() -> None:
    scanner = _load_secret_scan()
    scanner.findings.clear()
    secret = "sk-" + ("a" * 24)

    scanner.scan_text(f'api_key="{secret}"', "test")

    assert any(f.rule == "openai-like" for f in scanner.findings)
