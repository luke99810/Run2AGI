"""路径锁的单元测试 —— 前缀语义、归一化、原子性、序列化确定性。

并发那一条在 `tests/e2e/test_path_lock_concurrency.py`（README 里 A 的完成定义）。
这里测的是它下面那层：**什么算同一条路径、什么算重叠**。
归一化判错的话，并发测试照样全绿 —— 因为两个线程要的压根就不是同一条路径。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from codentum_contracts.state import OwnershipGraph, PacketId, PathLock
from codentum_control_plane.locks import LockTable, normalize_path

AT = "2026-08-03T10:00:00Z"
P1 = PacketId("wp-000001")
P2 = PacketId("wp-000002")


# ── 归一化 ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a/b", "a/b"),
        ("a/b/", "a/b"),
        ("./a/b", "a/b"),
        ("a//b", "a/b"),
        ("a/./b", "a/b"),
        ("a\\b", "a/b"),  # Windows 写法必须归一到同一条，否则可以绕开锁
        ("packages/control-plane/", "packages/control-plane"),
    ],
)
def test_normalize_collapses_equivalent_spellings(raw: str, expected: str) -> None:
    assert normalize_path(raw) == expected


@pytest.mark.parametrize("raw", ["", "/", "/abs/path", "C:/x", "a/../b", "..", "./"])
def test_normalize_rejects_dangerous_input(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_path(raw)


def test_equivalent_spellings_conflict_with_each_other() -> None:
    """归一化的意义：四种拼法只能有一个人拿到锁。"""
    table = LockTable()
    assert table.acquire(P1, ["packages/x"], at=AT).ok
    for spelling in ["packages/x/", "./packages/x", "packages//x", "packages\\x"]:
        assert not table.acquire(P2, [spelling], at=AT).ok, spelling


# ── 前缀语义 ─────────────────────────────────────────────────


def test_ancestor_and_descendant_both_conflict() -> None:
    table = LockTable()
    assert table.acquire(P1, ["packages/control-plane"], at=AT).ok

    descendant = table.acquire(P2, ["packages/control-plane/locks"], at=AT)
    assert not descendant.ok
    assert descendant.conflicts[0].relation == "ancestor"

    table2 = LockTable()
    assert table2.acquire(P1, ["packages/control-plane/locks"], at=AT).ok
    ancestor = table2.acquire(P2, ["packages/control-plane"], at=AT)
    assert not ancestor.ok
    assert ancestor.conflicts[0].relation == "descendant"


def test_sibling_with_shared_string_prefix_does_not_conflict() -> None:
    """★ `src/foo` 与 `src/foobar` 是两个目录，必须能并行。

    字符串 startswith 会把它们判成冲突 —— 方向安全但白吃并行度，
    而「并行度可计算」是这套设计被选中的理由之一。
    """
    table = LockTable()
    assert table.acquire(P1, ["src/foo"], at=AT).ok
    assert table.acquire(P2, ["src/foobar"], at=AT).ok


def test_case_only_difference_conflicts() -> None:
    """Windows / macOS 上 `Src/App.py` 与 `src/app.py` 是同一个文件。

    判成冲突是明知的误拒（Linux 上它们不同），选它是因为反方向漏判不可恢复。
    """
    table = LockTable()
    assert table.acquire(P1, ["src/app.py"], at=AT).ok
    assert not table.acquire(P2, ["Src/App.py"], at=AT).ok


def test_holder_of_reports_ancestor_owner() -> None:
    table = LockTable()
    table.acquire(P1, ["packages/x"], at=AT)
    assert table.holder_of("packages/x/deep/file.py") == P1
    assert table.holder_of("packages/y") is None


# ── 原子性 ───────────────────────────────────────────────────


def test_acquire_is_all_or_nothing() -> None:
    """一组路径里有一条撞车，整组都不落地。

    部分成功会让 packet 拿着半套写权限开工，而验收谓词是按全套写的。
    """
    table = LockTable()
    assert table.acquire(P1, ["packages/taken"], at=AT).ok
    before = table.version

    result = table.acquire(P2, ["packages/free-a", "packages/taken", "packages/free-b"], at=AT)
    assert not result.ok
    assert table.version == before, "被拒的申请不得推进版本号"
    assert table.holder_of("packages/free-a") is None, "★ 撞车前面那条也不许落地"
    assert table.holder_of("packages/free-b") is None


def test_self_overlapping_request_is_rejected() -> None:
    """packet 自己的 ownsPaths 内部就重叠 —— 是 packet 定义写错了，要让人看见。"""
    table = LockTable()
    result = table.acquire(P1, ["src", "src/app"], at=AT)
    assert not result.ok
    assert result.reason == "self_overlap"
    assert len(table.to_ownership().locks) == 0


def test_release_is_idempotent_and_does_not_bump_version() -> None:
    table = LockTable()
    table.acquire(P1, ["packages/x"], at=AT)
    v = table.version
    assert table.release(P1) == ("packages/x",)
    after_first = table.version
    assert after_first == v + 1

    assert table.release(P1) == ()
    assert table.version == after_first, "空释放不得推进版本 —— 否则乐观锁会看到不存在的变更"


# ── 序列化 ───────────────────────────────────────────────────


def test_to_ownership_is_deterministic() -> None:
    """输出顺序必须稳定：状态要进 Git，噪声 diff 会毁掉 P0 的证据。"""
    a = LockTable()
    for p in ["z/1", "a/2", "m/3"]:
        a.acquire(PacketId(f"wp-{abs(hash(p)) % 1000000:06d}"), [p], at=AT)

    first = [lk.pathPrefix for lk in a.to_ownership().locks]
    second = [lk.pathPrefix for lk in a.to_ownership().locks]
    assert first == second == sorted(first)


def test_from_ownership_revalidates_i1() -> None:
    """手工编辑过的 graph.json 不可信 —— 载入时必须重新校验，不能直接信。"""
    corrupt = OwnershipGraph(
        locks=(
            PathLock(pathPrefix="packages/x", heldBy=P1, acquiredAt=AT),
            PathLock(pathPrefix="packages/x/deep", heldBy=P2, acquiredAt=AT),
        ),
        version=7,
    )
    with pytest.raises(ValueError, match="I1"):
        LockTable.from_ownership(corrupt)


def test_roundtrip_preserves_version() -> None:
    table = LockTable()
    table.acquire(P1, ["packages/x"], at=AT)
    graph = table.to_ownership()
    restored = LockTable.from_ownership(graph)
    assert restored.version == graph.version
    assert restored.holder_of("packages/x") == P1


# ── TTL ──────────────────────────────────────────────────────


def test_release_expired_reclaims_stale_locks() -> None:
    table = LockTable()
    table.acquire(P1, ["packages/x"], at="2026-08-03T10:00:00Z")
    now = datetime(2026, 8, 3, 12, 0, 0, tzinfo=UTC)

    assert table.release_expired(now=now, ttl=timedelta(hours=3)) == ()
    assert table.release_expired(now=now, ttl=timedelta(hours=1)) == ("packages/x",)
    assert table.holder_of("packages/x") is None


def test_unparsable_timestamp_counts_as_expired() -> None:
    """时间戳坏掉的锁若永不过期，它占住的路径就再没人能拿到 —— 而锁表看起来完全正常。"""
    broken = PathLock(pathPrefix="packages/x", heldBy=P1, acquiredAt="not-a-time")
    table = LockTable.from_ownership(OwnershipGraph(locks=(broken,), version=1))
    now = datetime(2026, 8, 3, 12, 0, 0, tzinfo=UTC)
    assert table.release_expired(now=now, ttl=timedelta(days=365)) == ("packages/x",)
