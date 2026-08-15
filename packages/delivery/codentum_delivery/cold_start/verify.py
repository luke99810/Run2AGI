"""冷启动验证 —— 在一个什么都没装的容器里，只拿交付包，判断它能不能交付。

════════════════════════════════════════════════════════════════
 ★ 它验的不是代码对不对，是「能不能交付」
════════════════════════════════════════════════════════════════

`docker/README.md` 把绝大多数「在我机器上是好的」的死因列成了四条：
漏装的依赖、写死的本机路径、没记录的环境变量、**手工执行过但没写进脚本的那一步**。
这个模块能确定性判定的是前两条，第三条给出线索，第四条只有真跑才知道。

★ 所以它的报告分成 `problems` 与 `warnings` 两栏，**不合并**：
  problems 是「这个包坏了」，warnings 是「这个包可能带不走」。
  合成一个布尔会让后者要么被当成故障（挡住正常交付），
  要么被吞掉（那就等于没验）。

════════════════════════════════════════════════════════════════
 ★ 为什么这个文件只许 import 标准库
════════════════════════════════════════════════════════════════

它要在 `python:3.11-slim` 里**独立运行** —— 那个镜像里没有 Codentum，
也不该有：装了 Codentum 再去验交付包，验的就不是「从零」了。
所以它是单文件、零依赖，`docker/cold-start/Dockerfile` 直接 COPY 它。

★ 这条约束有测试守着（`test_cold_start_verify.py`）——
  哪天有人在这里 `from codentum_contracts import ...`，
  容器里会在**运行时**炸，而那时候镜像已经推出去了。

════════════════════════════════════════════════════════════════
 ★ 交付包格式来自桌面端的打包器，不是这里定义的
════════════════════════════════════════════════════════════════

`packages/desktop/shell/main/artifact-packager.ts` 产出：

    delivery.tar.gz
      ├── CODENTUM-DELIVERY.json   # schema=codentum.source-delivery.v1，逐文件 sha256
      └── project/<原样路径>...

★ 判据用的**真实包**由那个打包器生成，不是这里手搓的 fixture ——
  跨语言的格式约定，手搓 fixture 只能证明我理解得自洽，
  证明不了两边说的是同一件事。本仓库已经吃过「fixture 假了」的亏。
"""

from __future__ import annotations

import hashlib
import json
import re
import tarfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

__all__ = [
    "MANIFEST_NAME",
    "SCHEMA",
    "VerificationReport",
    "verify_delivery",
]

MANIFEST_NAME = "CODENTUM-DELIVERY.json"
SCHEMA = "codentum.source-delivery.v1"

_PROJECT_PREFIX = "project/"

_HOST_PATH_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    ("Windows 绝对路径", re.compile(rb"[A-Za-z]:[\\/](?:Users|Program Files|Anaconda|Python)")),
    ("Linux 家目录", re.compile(rb"/home/[A-Za-z0-9_.-]+/")),
    ("macOS 家目录", re.compile(rb"/Users/[A-Za-z0-9_.-]+/")),
)
"""写死的本机路径 —— `docker/README.md` 给冷启动列的头号死因。

★ 只在**文本文件**里找，且结果进 `warnings` 不进 `problems`：
  一个讲部署的 Markdown 里出现 `/home/xxx/` 完全正常。
  把它判成故障会挡住正常交付，而一旦挡错一次，
  下一步就是有人把整项检查关掉 —— 那比没有检查更糟。
"""

_TEXT_SUFFIXES = frozenset(
    {
        ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".toml", ".yaml", ".yml",
        ".ini", ".cfg", ".sh", ".ps1", ".bat", ".md", ".txt", ".env.example",
        ".gradle", ".properties", ".xml", ".html", ".css", ".sql",
    }
)

_MAX_SCAN_BYTES = 1 * 1024 * 1024
"""单文件扫描上限。超过就跳过并记一条 warning —— 不静默略过。"""


@dataclass(frozen=True, slots=True)
class VerificationReport:
    """一次冷启动验证的结果。

    ★ `ok` 只由 `problems` 决定。warnings 不影响它 ——
      见模块开头那段：两栏合一会让检查要么误伤要么失效。
    """

    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    file_count: int = 0
    total_bytes: int = 0
    packet_id: str | None = None
    created_at: str | None = None

    @property
    def ok(self) -> bool:
        return not self.problems

    def render(self) -> str:
        lines = [
            "═" * 68,
            " 冷启动交付验证",
            "═" * 68,
            f"  文件数：{self.file_count}    总字节：{self.total_bytes}",
            f"  packet：{self.packet_id or '—'}    打包于：{self.created_at or '—'}",
        ]
        if self.problems:
            lines.append("\n✗ 这个交付包坏了：")
            lines.extend(f"    · {p}" for p in self.problems)
        else:
            lines.append("\n✓ 包体完整：清单、文件数、逐文件 SHA-256 全部一致。")
        if self.warnings:
            # ★ 即使 ok 也要打出来：「能解开」不等于「能在别的机器上跑起来」。
            lines.append("\n⚠ 可能带不走（不影响包体完整性，但会让冷启动失败）：")
            lines.extend(f"    · {w}" for w in self.warnings)
        lines.append("═" * 68)
        return "\n".join(lines)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unsafe_member(name: str) -> str | None:
    """判定归档成员路径是否会写到解包目录之外（tar-slip）。

    ★ 这一层不能省，也不能只靠解包时的目录判断：拿到交付包的人
      可能直接 `tar xzf`，而那条命令不做任何越界检查。
      **验证器要在解包之前就能说「这个包不能解」。**
    """

    if name.startswith("/") or name.startswith("\\"):
        return f"绝对路径成员：{name}"
    if re.match(r"^[A-Za-z]:", name):
        return f"带盘符的成员：{name}"
    parts = PurePosixPath(name.replace("\\", "/")).parts
    if ".." in parts:
        return f"含上跳段的成员：{name}"
    return None


def verify_delivery(archive: Path | str) -> VerificationReport:
    """校验一个交付包，返回报告（不抛异常，除非文件根本读不了）。

    ★ 不抛而是返回报告：调用方是容器的 entrypoint，
      它要把**所有**问题一次列全给人看，而不是撞上第一个就退出。
      一次只报一个问题的验证器，会让人来回跑五遍才修完。
    """

    path = Path(archive)
    problems: list[str] = []
    warnings: list[str] = []

    if not path.is_file():
        return VerificationReport(problems=[f"找不到交付包：{path}"])

    try:
        with tarfile.open(path, "r:gz") as tar:
            members = {m.name: m for m in tar.getmembers() if m.isfile()}
            payload: dict[str, bytes] = {}
            for name, member in members.items():
                unsafe = _unsafe_member(name)
                if unsafe is not None:
                    problems.append(unsafe)
                    continue
                handle = tar.extractfile(member)
                if handle is None:  # pragma: no cover - isfile 已保证
                    problems.append(f"成员读不出内容：{name}")
                    continue
                payload[name] = handle.read()
    except (tarfile.TarError, OSError) as exc:
        return VerificationReport(problems=[f"交付包无法解开：{exc}"])

    raw_manifest = payload.get(MANIFEST_NAME)
    if raw_manifest is None:
        problems.append(f"交付包缺少 {MANIFEST_NAME}")
        return VerificationReport(problems=problems, warnings=warnings)

    try:
        manifest = json.loads(raw_manifest.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return VerificationReport(problems=[f"{MANIFEST_NAME} 不是合法 JSON：{exc}"])

    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        got = manifest.get("schema") if isinstance(manifest, dict) else type(manifest).__name__
        # ★ 报出实际拿到的值，不只说「格式不对」——
        #   格式漂移时，那个值就是唯一的线索。
        problems.append(f"清单 schema 不是 {SCHEMA}（实际：{got!r}）")
        return VerificationReport(problems=problems, warnings=warnings)

    declared = manifest.get("files")
    if not isinstance(declared, list):
        return VerificationReport(problems=["清单里的 files 不是数组"])

    total_bytes = 0
    seen: set[str] = set()
    for item in declared:
        if not isinstance(item, dict):
            problems.append(f"清单条目不是对象：{item!r}")
            continue
        rel = str(item.get("path", ""))
        member_name = _PROJECT_PREFIX + rel
        seen.add(member_name)
        content = payload.get(member_name)
        if content is None:
            problems.append(f"清单声明了但包里没有：{rel}")
            continue
        size = item.get("sizeBytes")
        if isinstance(size, int) and len(content) != size:
            problems.append(f"大小不一致：{rel}（清单 {size}，实际 {len(content)}）")
        digest = str(item.get("sha256", ""))
        actual = _sha256(content)
        if digest and actual != digest:
            problems.append(f"SHA-256 不一致：{rel}（清单 {digest[:12]}…，实际 {actual[:12]}…）")
        total_bytes += len(content)

    # ★ 反向也要查：包里有、清单里没有的文件是**未声明的夹带**。
    #   只查一个方向的校验，等于允许往包里塞东西。
    for name in payload:
        if name == MANIFEST_NAME:
            continue
        if name not in seen:
            problems.append(f"包里有但清单未声明：{name}")

    warnings.extend(_scan_host_paths(payload))

    return VerificationReport(
        problems=problems,
        warnings=warnings,
        file_count=len(declared),
        total_bytes=total_bytes,
        packet_id=manifest.get("packetId"),
        created_at=manifest.get("createdAt"),
    )


def _scan_host_paths(payload: dict[str, bytes]) -> list[str]:
    """在文本文件里找写死的本机路径。

    ★ 结果一律是 warning：见 `_HOST_PATH_PATTERNS` 的说明。
      挡错一次，下一步就是有人把整项检查关掉。
    """

    found: list[str] = []
    for name, content in sorted(payload.items()):
        if name == MANIFEST_NAME:
            continue
        if PurePosixPath(name).suffix.lower() not in _TEXT_SUFFIXES:
            continue
        if len(content) > _MAX_SCAN_BYTES:
            found.append(f"{name}：超过 {_MAX_SCAN_BYTES} 字节，未扫描（不是「没问题」）")
            continue
        for label, pattern in _HOST_PATH_PATTERNS:
            match = pattern.search(content)
            if match is not None:
                snippet = match.group(0).decode("utf-8", "replace")
                found.append(f"{name}：疑似{label} `{snippet}`")
                break
    return found


def main(argv: list[str] | None = None) -> int:
    import sys

    # ★ 先把输出流切成 UTF-8，再打任何东西。
    #
    #   实测：在 GBK 控制台上直接 print(report.render()) 会抛
    #   `UnicodeEncodeError: 'gbk' codec can't encode character '✓'` ——
    #   而那时候**校验已经通过了**，崩在打印上，退出码变成 1。
    #   一个「包是好的」被报成「包坏了」，比不检查更坏。
    #
    #   ★ 这是本仓库第四次栽在编码上（EvidenceRef 分隔符、流编码、
    #     git quotepath，加这一次）。共同形状是：**功能是对的，
    #     出口把它毁了**，而单元测试摸不到出口。
    #
    #   errors="replace" 而不是让它抛：报告的价值在于被人看到，
    #   某个字符画不出来也要把其余内容送达。
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):  # pragma: no cover - 极少数不可重配的流
                pass

    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("用法：python verify.py <delivery.tar.gz>", file=sys.stderr)
        return 2
    report = verify_delivery(args[0])
    print(report.render())
    # ★ 退出码只反映 problems。warnings 不影响它 ——
    #   否则 CI 会因为一句文档里的 /home/ 而红，然后这项检查被关掉。
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
