"""Scan text files in an already unpacked delivery bundle.

This complements, and never replaces, the mandatory repository + Git-history scan.
Binary executables are verified by provenance, hashes, and signing rather than by
decoding arbitrary bytes as text.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .scanner import ScanUnavailable, scan_worktree


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan unpacked bundle text for credentials")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--label", default="bundle")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        count, findings = scan_worktree(args.root)
    except (OSError, ScanUnavailable) as exc:
        print(json.dumps({"status": "unable", "reason": str(exc)}) if args.json else f"bundle-scan UNABLE: {exc}")
        return 2
    if args.json:
        print(json.dumps({
            "status": "failed" if findings else "passed",
            "label": args.label,
            "textFiles": count,
            "findings": [
                {"path": item.path, "line": item.line, "rule": item.rule, "preview": item.redacted_preview}
                for item in findings
            ],
        }, ensure_ascii=False))
    elif findings:
        print(f"bundle-scan FAILED ({args.label}): {len(findings)} possible credential(s)")
        for item in findings:
            print(f"  {item.path}:{item.line} [{item.rule}] {item.redacted_preview}")
    else:
        print(f"bundle-scan PASSED ({args.label}): {count} unpacked text files scanned")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
