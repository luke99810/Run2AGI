"""容器定义门禁 —— 把 docker/README.md 的硬约束变成会变红的判据。

════════════════════════════════════════════════════════════════
 ★ 为什么需要它
════════════════════════════════════════════════════════════════

`docker/README.md` 立了四条硬约束（不烘焙凭证、冷启动从零、镜像 pin 版本、
每个 Dockerfile 顶部写清用途）。在此之前它们只是**文档里的话** ——
没有任何东西会因为违反它们而变红。

而这几条恰好都是「违反了也看起来一切正常」的类型：

  · 烘焙了凭证   → 镜像照常能跑，泄漏要等 push 出去才发生
  · 用了 latest   → 今天绿，下个月红，而且看不出是基础镜像变了
  · 没写用途     → 半年后没人知道这个容器该不该动

★ 特别说明它**不能**做什么：它不构建镜像。
  Dockerfile 语法对不对、镜像建不建得出来，只有 `docker build` 知道。
  所以这个脚本通过 ≠ 镜像能建 —— 输出里会明说这一点，
  免得它变成又一个「看起来在检查、实际没在拦」的东西。

════════════════════════════════════════════════════════════════
 ★ 为什么 digest 缺失只是提醒，不是失败
════════════════════════════════════════════════════════════════

README 的原文是「镜像要 pin 版本（不用 latest）」。用具名 tag 已经满足这条。
pin 到 digest 更严，但取 digest 必须连 registry 真拉一次 ——
把「没连过网」判成违规，会让离线环境下这道门禁恒红，
然后有人把它关掉。**判据要在它能判定的范围内判定。**
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCKER = REPO / "docker"


@dataclass(frozen=True, slots=True)
class Finding:
    path: str
    rule: str
    detail: str


_CREDENTIAL_KEYS = re.compile(
    r"(API_?KEY|SECRET|TOKEN|PASSWORD|PASSWD|ACCESS_?KEY|PRIVATE_?KEY|CREDENTIAL)",
    re.IGNORECASE,
)
"""凭证类变量名。

★ **不能加 `\\b`。** 第一版写的是 `\\bAPI_KEY\\b`，而真实的变量名是
  `DASHSCOPE_API_KEY` —— 下划线是单词字符，`E` 和 `_` 之间没有词边界，
  于是那条规则一次也没命中过。

★ 这个错误是**因果检验抓出来的**：四条规则逐条构造违例去试，
  只有凭证那条退出码仍是 0。不做那一步的话，这道门禁会带着
  四分之一的死规则上线，而它每次都报「✓ 四条硬约束全部通过」——
  一句会撒谎的绿灯。
"""

_PLACEHOLDER = re.compile(r"^\s*(#|$)")


def _dockerfiles() -> list[Path]:
    return sorted(DOCKER.rglob("Dockerfile*"))


def check_purpose_comment(path: Path, lines: list[str]) -> list[Finding]:
    """硬约束 4：每个 Dockerfile 顶部写一句「这个容器是干什么的」。"""

    head = "\n".join(lines[:6])
    if "这个容器是干什么的" not in head:
        return [
            Finding(
                path.name,
                "用途说明",
                "顶部 6 行内没有「这个容器是干什么的」—— 半年后没人知道它该不该动",
            )
        ]
    return []


def check_pinned_base(path: Path, lines: list[str]) -> list[Finding]:
    """硬约束 3：镜像要 pin 版本，不用 latest。"""

    findings: list[Finding] = []
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped.upper().startswith("FROM "):
            continue
        image = stripped.split(None, 1)[1].split(" AS ")[0].strip()
        if image.endswith(":latest") or ":" not in image.rsplit("/", 1)[-1]:
            findings.append(
                Finding(
                    path.name,
                    "版本 pin",
                    f"第 {number} 行 `{image}` 没有具名 tag（或用了 latest）——"
                    "冷启动的意义在于可复现，latest 会让它今天绿明天红",
                )
            )
    return findings


def check_no_baked_credentials(path: Path, lines: list[str]) -> list[Finding]:
    """硬约束 1：容器里不烘焙凭证。

    ★ 只看 ENV / ARG 的**赋值**，不看注释 —— 注释里写
      「凭证不进镜像层」是正确做法，把它判成违规会让人删掉那条注释。
      这正是本仓库那条教训：判据要认语义，不要认字符串。
    """

    findings: list[Finding] = []
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if _PLACEHOLDER.match(stripped):
            continue
        upper = stripped.upper()
        if not (upper.startswith("ENV ") or upper.startswith("ARG ")):
            continue
        if "=" not in stripped:
            # `ARG FOO`（无默认值）是运行时注入，正确用法
            continue
        name, _, value = stripped.partition("=")
        if not _CREDENTIAL_KEYS.search(name):
            continue
        if value.strip() in {"", '""', "''"}:
            continue
        findings.append(
            Finding(
                path.name,
                "凭证烘焙",
                f"第 {number} 行给凭证类变量写了默认值：`{stripped[:60]}` —— "
                "镜像层删不掉，push 出去就是永久泄漏",
            )
        )
    return findings


def check_cold_start_is_from_zero(path: Path, lines: list[str]) -> list[Finding]:
    """硬约束 2：cold-start 必须从零，不许复用宿主机缓存。

    ★ 能确定性判定的是「有没有把整个仓库 COPY 进去」——
      COPY 了 Codentum 的源码，被验的项目就能在容器里看到它，
      那就不是「只拿交付产物」了。
    """

    if path.parent.name != "cold-start":
        return []
    findings: list[Finding] = []
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped.upper().startswith("COPY "):
            continue
        parts = stripped.split()
        if len(parts) >= 3 and parts[1] in {".", "./", "/"}:
            findings.append(
                Finding(
                    path.name,
                    "冷启动从零",
                    f"第 {number} 行把整个上下文 COPY 进镜像 —— "
                    "被验的项目会看到 Codentum 源码，那就不是「从零」了",
                )
            )
    return findings


def check_entrypoint_scripts_are_lf() -> list[Finding]:
    """Windows 开发 + Linux 容器的坑：`.sh` 带 CRLF 会报
    `/bin/sh^M: bad interpreter`，而那个报错完全不指向真实原因。

    ★ 三个人都在 Windows 上开发，这个坑一定会踩。
      `.gitattributes` 里的 `*.sh text eol=lf` 是第一道防线，
      这里是第二道 —— 因为工作区里的文件才是被 COPY 进镜像的那个。
    """

    findings: list[Finding] = []
    for script in sorted(DOCKER.rglob("*.sh")):
        raw = script.read_bytes()
        if b"\r\n" in raw:
            findings.append(
                Finding(
                    script.relative_to(REPO).as_posix(),
                    "换行符",
                    "含 CRLF —— 在容器里会报 `/bin/sh^M: bad interpreter`，"
                    "而那个报错不指向真实原因",
                )
            )
        if not raw.startswith(b"#!"):
            findings.append(
                Finding(script.relative_to(REPO).as_posix(), "换行符", "缺少 shebang")
            )
    return findings


def main() -> int:
    files = _dockerfiles()
    print("═" * 72)
    print(" 容器定义门禁")
    print("═" * 72)

    if not files:
        # ★ 零输入的绿灯最危险 —— 没有 Dockerfile 要报「无法执行」，
        #   不是「通过」。
        print("\n✗ docker/ 下没有找到任何 Dockerfile —— 这道门禁无法执行。")
        return 1

    findings: list[Finding] = []
    for path in files:
        lines = path.read_text(encoding="utf-8").splitlines()
        findings.extend(check_purpose_comment(path, lines))
        findings.extend(check_pinned_base(path, lines))
        findings.extend(check_no_baked_credentials(path, lines))
        findings.extend(check_cold_start_is_from_zero(path, lines))
    findings.extend(check_entrypoint_scripts_are_lf())

    print(f"\n检查了 {len(files)} 个 Dockerfile：")
    for path in files:
        print(f"  · {path.relative_to(REPO).as_posix()}")

    if findings:
        print(f"\n✗ 发现 {len(findings)} 处违反 docker/README.md 的硬约束：\n")
        for item in findings:
            print(f"  [{item.rule}] {item.path}")
            print(f"      {item.detail}")
        return 1

    print("\n✓ 四条硬约束全部通过：用途说明 · 版本 pin · 不烘焙凭证 · 冷启动从零")
    # ★ 说清它没验什么 —— 否则它就成了又一个「看起来在检查」的东西。
    print("\n⚠ 它**没有**构建镜像。Dockerfile 语法对不对、镜像建不建得出来，")
    print("   只有 `docker build` 知道。这道门禁通过 ≠ 镜像能建。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
