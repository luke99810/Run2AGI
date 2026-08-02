#!/usr/bin/env python
"""check_boundaries —— 把 boundaries.yaml 从「约定」变成「被强制」

════════════════════════════════════════════════════════════════
 为什么必须有这个脚本
════════════════════════════════════════════════════════════════

没有它，boundaries.yaml 只是一份君子协定 ——
而这套系统全部的论点就是【约定靠不住，约束才可靠】。
我们不能在自己的仓库里违反自己的核心主张。

════════════════════════════════════════════════════════════════
 检查项
════════════════════════════════════════════════════════════════

  结构      members / modules / invariants 齐全，owner 都在 members 里
  I1        ★ 两个【有主】module 的 paths 不得相交
  生成物     owner: null 可从有主区域里抠出【更具体】的一块，但不能反过来
  覆盖      packages/ 下每个包必须恰好一个 owner
  存在性     声明的路径前缀必须真的存在（打错字会让规则永远不命中）
  I3        ★ 改 packages/contracts/** 的提交，作者必须是 A（需 git）
  冻结      frozen_at 一旦填写，必须是合法日期

依赖：零。用法：
    python scripts/check_boundaries.py
    python scripts/check_boundaries.py --no-git
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from lib.console import setup_console  # noqa: E402
from lib.yaml_lite import YamlLiteError, parse_yaml  # noqa: E402

setup_console()

ROOT = Path(__file__).resolve().parent.parent
FILE = ROOT / "boundaries.yaml"

errors: list[str] = []
notes: list[str] = []


def err(m: str) -> None:
    errors.append(m)


def fail(msg: str) -> None:
    print(f"\n✗ check-boundaries 失败\n\n  {msg}\n", file=sys.stderr)
    raise SystemExit(1)


def git(args: list[str], quiet: bool = False) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def is_git_repo() -> bool:
    try:
        git(["rev-parse", "--git-dir"])
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def has_commits() -> bool:
    try:
        git(["rev-parse", "--verify", "HEAD"])
        return True
    except subprocess.CalledProcessError:
        return False


def norm(p: str) -> str:
    """packages/contracts/** → packages/contracts/ ；具体文件保持原样。"""
    return re.sub(r"\*+$", "", str(p))


def overlaps(a: str, b: str) -> bool:
    return a.startswith(b) or b.startswith(a)


def main() -> None:
    if not FILE.exists():
        fail("找不到 boundaries.yaml")

    try:
        doc: dict[str, Any] = parse_yaml(FILE.read_text(encoding="utf-8"))
    except YamlLiteError as e:
        fail(f"boundaries.yaml 解析失败：{e}")

    for k in ("version", "members", "modules", "invariants"):
        if k not in doc:
            err(f'boundaries.yaml 缺顶层字段 "{k}"')

    members: dict[str, Any] = doc.get("members") or {}
    modules: list[dict[str, Any]] = doc.get("modules") or []

    seen: set[str] = set()
    for m in modules:
        name = m.get("name")
        if not name:
            err("有 module 缺 name")
            continue
        if name in seen:
            err(f"module 名重复：{name}")
        seen.add(name)
        if not m.get("paths"):
            err(f'module "{name}" 的 paths 为空')
        if m.get("owner") is not None and m["owner"] not in members:
            err(f'module "{name}" 的 owner "{m["owner"]}" 不在 members（{"/".join(members)}）里')

    owned = [m for m in modules if m.get("owner") is not None]
    generated = [m for m in modules if m.get("owner") is None]

    # ── I1：有主 module 两两不相交 ────────────────────────
    for i, a in enumerate(owned):
        for b in owned[i + 1 :]:
            for pa in a.get("paths") or []:
                for pb in b.get("paths") or []:
                    if overlaps(norm(pa), norm(pb)):
                        err(
                            f'★ I1 违反 —— "{a["name"]}"({a["owner"]}) 的 {pa} 与 '
                            f'"{b["name"]}"({b["owner"]}) 的 {pb} 路径相交。\n'
                            f"      单写者是这套设计的地基：两个 owner 共享一段路径，"
                            f"合并冲突就重新变成可能。"
                        )

    # ── 生成物的例外 ──────────────────────────────────────
    # generated（owner: null）允许落在某个有主区域【内部】——
    # 它是"从 A 的地盘里抠出一块谁都不许手写的地方"，
    # 属于更具体的规则覆盖更一般的规则，和 .gitignore 的否定模式同理。
    # 但要求【严格更具体】，否则就不是抠洞而是抢地盘。
    for g in generated:
        for gp in g.get("paths") or []:
            n = norm(gp)
            clash = next(
                (o for o in owned if any(norm(op).startswith(n) and norm(op) != n for op in o.get("paths") or [])),
                None,
            )
            host = next(
                (o for o in owned if any(n.startswith(norm(op)) and n != norm(op) for op in o.get("paths") or [])),
                None,
            )
            if clash:
                err(
                    f'★ 生成物 "{gp}"（module {g["name"]}）覆盖了有主 module "{clash["name"]}" 的整个范围。\n'
                    f"      生成物只能从有主区域里抠出【更具体】的一块，不能反过来把有主区域包进去。"
                )
            elif host:
                notes.append(f'生成物 {gp} 落在 {host["name"]}({host["owner"]}) 内部 —— 合法的"抠洞"')

    for i, a in enumerate(generated):
        for b in generated[i + 1 :]:
            for pa in a.get("paths") or []:
                for pb in b.get("paths") or []:
                    if overlaps(norm(pa), norm(pb)):
                        err(f'两条生成物规则相交：{pa}（{a["name"]}）与 {pb}（{b["name"]}）')

    # ── 覆盖：packages/ 下每个包恰好一个 owner ────────────
    pkg_dir = ROOT / "packages"
    if pkg_dir.is_dir():
        for pkg in sorted(p for p in pkg_dir.iterdir() if p.is_dir()):
            path = f"packages/{pkg.name}/"
            hits = [m for m in owned if any(path.startswith(norm(p)) for p in m.get("paths") or [])]
            if not hits:
                err(f"packages/{pkg.name}/ 没有 owner。★ 新增目录要先在 boundaries.yaml 登记，再写代码。")
            elif len(hits) > 1:
                who = "、".join(f'{h["name"]}({h["owner"]})' for h in hits)
                err(f"packages/{pkg.name}/ 有 {len(hits)} 个 owner：{who}")

    # ── 存在性：打错字的规则永远不命中 ────────────────────
    for m in modules:
        for p in m.get("paths") or []:
            base = norm(p).rstrip("/")
            if base and not (ROOT / base).exists():
                err(
                    f'module "{m["name"]}" 声明的路径 {p} 在仓库里不存在。\n'
                    f"      ★ 打错字的规则不会报错，只会永远不命中 —— 那比没有规则更糟。"
                )

    # ── I3：改 contracts 的提交必须是 A ───────────────────
    contracts_mod = next(
        (m for m in owned if any(norm(p) == "packages/contracts/" for p in m.get("paths") or [])), None
    )

    if "--no-git" in sys.argv:
        notes.append("已跳过 I3 契约冻结检查（--no-git）")
    elif not is_git_repo():
        notes.append("⚠️ 还不是 git 仓库，I3 契约冻结检查【无法执行】（不是通过）。`git init` 之后生效。")
    elif contracts_mod is None:
        err("boundaries.yaml 里找不到拥有 packages/contracts/ 的 module —— I3 无从检查")
    else:
        owner_id = contracts_mod["owner"]
        expected = (members.get(owner_id) or {}).get("gitAuthor")
        if not expected:
            # ★ commit 里记的是真实姓名，boundaries.yaml 里写的是 A/B/C。
            #   对不上就【无法执行】而不是【通过】——
            #   一个因为配置缺失而静默放行的门禁，比没有门禁更危险。
            err(
                f"★ I3 无法执行 —— members.{owner_id}.gitAuthor 未填。\n"
                f"      commit 里记的是 git user.name，boundaries.yaml 里写的是 {owner_id}，\n"
                f'      两者对不上就无法判断"是不是只有 {owner_id} 改了 contracts"。\n'
                f"      让 {owner_id} 跑 `git config user.name`，把结果逐字填进 boundaries.yaml。"
            )
        elif not has_commits():
            # ★ 空仓库要单独识别。不然 git log 返回空，检查会"通过"——
            #   而那个通过与"作者都合规"长得一模一样。
            #   零输入的绿灯是最危险的绿灯：它看起来像证明，实际什么都没证明。
            notes.append("⚠️ 仓库还没有任何提交，I3 契约冻结检查【无法执行】（不是通过）。首次提交后自动生效。")
        else:
            log = git(["log", "--format=%H%x09%an", "--", "packages/contracts"])
            commits = [ln.split("\t") for ln in log.splitlines() if ln]
            offenders = [(h, a) for h, a in commits if a != expected]
            if offenders:
                lst = "\n".join(f"      {h[:8]} by {a}" for h, a in offenders)
                err(
                    f"★ I3 契约冻结违反 —— 以下提交改了 packages/contracts/ "
                    f"但作者不是 {expected}（{owner_id}）：\n{lst}\n"
                    f"      别人自己改契约，会让另外两人的代码在毫无征兆的情况下失配。"
                )
            elif not commits:
                notes.append("⚠️ 尚无任何触及 packages/contracts/ 的提交，I3 暂时无内容可查。")
            else:
                notes.append(f"I3 契约冻结：{len(commits)} 个触及 contracts 的提交，作者均为 {expected}（{owner_id}）")

    # ── 冻结状态 ──────────────────────────────────────────
    frozen = doc.get("frozen_at")
    if frozen is None:
        notes.append("⚠️ frozen_at 仍为 null —— 契约尚未冻结。第 0 周结束时由全员确认后填入日期。")
    elif not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(frozen)):
        err(f'frozen_at "{frozen}" 不是合法日期（应为 YYYY-MM-DD）')
    else:
        notes.append(f"契约已冻结于 {frozen} —— 此后改 packages/contracts/** 需走变更窗口 + ADR")

    for n in notes:
        print(f"  · {n}")

    if errors:
        print(f"\n✗ check-boundaries：{len(errors)} 处问题\n", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        print("", file=sys.stderr)
        raise SystemExit(1)

    print(
        f"\n✓ boundaries.yaml 通过：{len(modules)} 个 module"
        f"（{len(owned)} 有主 / {len(generated)} 生成物），路径独占成立，packages/ 全覆盖"
    )


if __name__ == "__main__":
    main()
