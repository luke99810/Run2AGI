"""CLI for the mandatory worktree + complete Git history gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .scanner import ScanUnavailable, scan_repository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan both the working tree and every reachable Git blob. No bypass flag exists."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Git worktree to scan")
    parser.add_argument("--json", action="store_true", help="emit a redacted machine-readable report")
    args = parser.parse_args(argv)
    try:
        report = scan_repository(args.root)
    except (OSError, ScanUnavailable) as exc:
        if args.json:
            print(json.dumps({"status": "unable", "reason": str(exc)}, ensure_ascii=False))
        else:
            print(f"secret-scan UNABLE: {exc}")
        return 2
    if args.json:
        print(
            json.dumps(
                {
                    "status": "failed" if report.findings else "passed",
                    "worktreeFiles": report.worktree_files,
                    "historyBlobs": report.history_blobs,
                    "findings": [
                        {
                            "source": item.source,
                            "path": item.path,
                            "line": item.line,
                            "rule": item.rule,
                            "preview": item.redacted_preview,
                        }
                        for item in report.findings
                    ],
                },
                ensure_ascii=False,
            )
        )
    elif report.findings:
        print(f"secret-scan FAILED: {len(report.findings)} possible credential(s)")
        for item in report.findings:
            print(f"  {item.source} {item.path}:{item.line} [{item.rule}] {item.redacted_preview}")
    else:
        print(
            f"secret-scan PASSED: {report.worktree_files} worktree files and "
            f"{report.history_blobs} historical blobs scanned"
        )
    return 1 if report.findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
