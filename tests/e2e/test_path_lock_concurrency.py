"""路径锁的并发验收 —— A 第一件事的【完成定义】。

README「契约冻结后，各人的第一件事」里，A 那一格的完成定义逐字是：

    并发申请重叠路径，有且只有一个成功

graph.schema.json 里也写着 I1「不由 schema 保证……由 e2e 并发用例验证」。
所以这个文件就是那个用例，它红了就等于 I1 没有强制点。

★ 用真线程 + Barrier，不用 mock。
  锁的 bug 几乎全部出在 check-then-act 之间被穿插的那一瞬 —— 串行地调
  两次 acquire 永远测不出来，它测的是逻辑，不是并发。
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from codentum_contracts.state import PacketId
from codentum_control_plane.locks import AcquireResult, LockTable

AT = "2026-08-03T10:00:00Z"


@pytest.fixture(autouse=True)
def _tight_gil_switching() -> Iterator[None]:
    """把 GIL 的切换间隔压到 1µs，让 check-then-act 的窗口真的会被穿插。

    ★ 这条不是调优，是让测试**有能力失败**。
      实测过：默认 5ms 间隔下，把 LockTable 的互斥锁整个换成空操作，
      跑 400 轮「五个线程抢重叠路径」，依然每轮都恰好一个赢家 —— 因为临界区
      太短，GIL 根本没机会在中间切走。那样的绿灯什么都没证明。
      压到 1µs 后，无锁版本会立刻暴露（计数器与树不一致 / 多个赢家）。

    ★ 这也说明一件更一般的事：**并发测试默认是空转的**。
      不主动制造穿插，它测的只是"串行调用两次"。
    """
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        yield
    finally:
        sys.setswitchinterval(previous)

# 两两之间都构成前缀关系（同路径 / 祖先 / 后代 三种关系都覆盖到）
OVERLAPPING = [
    "packages",
    "packages/control-plane",
    "packages/control-plane/locks",
    "packages/control-plane/locks/path_lock.py",
    "packages/control-plane",  # 与第 2 条完全相同
]


def _pid(n: int) -> PacketId:
    return PacketId(f"wp-{n:06d}")


def _race(fn: object, n: int) -> list[AcquireResult]:
    """让 n 个线程尽量在同一瞬间进入被测函数。"""
    barrier = threading.Barrier(n)

    def run(i: int) -> AcquireResult:
        barrier.wait()
        return fn(i)  # type: ignore[operator]  # 调用方传的是 (int) -> AcquireResult

    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(run, range(n)))


def test_overlapping_paths_exactly_one_wins() -> None:
    """★ 完成定义本身：并发申请重叠路径，有且只有一个成功。"""
    table = LockTable()

    results = _race(lambda i: table.acquire(_pid(i), [OVERLAPPING[i]], at=AT), len(OVERLAPPING))

    winners = [r for r in results if r.ok]
    assert len(winners) == 1, (
        f"期望恰好 1 个成功，实际 {len(winners)} 个。"
        f"★ >1 说明 I1 被破坏（两个 packet 同时持有重叠路径）；"
        f"0 说明锁表把所有人都挡住了，同样是 bug。"
    )

    losers = [r for r in results if not r.ok]
    assert all(r.reason == "conflict" for r in losers), [r.reason for r in losers]
    # 每个失败者都要说清楚撞上了谁 —— 没有可操作理由的拒绝会让调度器只能盲目重试
    assert all(r.conflicts for r in losers)

    # 锁表里最终只有一条锁，且就是赢家申请的那条
    ownership = table.to_ownership()
    assert len(ownership.locks) == 1
    assert ownership.locks[0].pathPrefix == winners[0].acquired[0]


def test_same_version_cas_exactly_one_wins() -> None:
    """乐观锁那一层：路径互不重叠，但都基于同一个版本号提交。

    ★ 这条测的是**跨进程**的那半边。互斥锁只保护进程内；两个进程各自读到
      version=0、各自改、各自写回 graph.json，后写的会静默覆盖先写的 ——
      路径明明不冲突，变更却丢了一次。version 就是拦这个的。
    """
    table = LockTable()
    base = table.version

    results = _race(
        lambda i: table.acquire(_pid(i), [f"packages/mod-{i}"], at=AT, expected_version=base),
        6,
    )

    winners = [r for r in results if r.ok]
    assert len(winners) == 1, f"同一版本号并发提交应只有一个成功，实际 {len(winners)} 个"
    assert all(r.reason == "stale_version" for r in results if not r.ok)
    assert table.version == base + 1


def test_disjoint_paths_all_succeed() -> None:
    """反向保证：互不重叠的申请必须全部成功。

    ★ 少了这条，一个「无论如何都拒绝」的实现也能通过上面两条测试。
      而并行度正是这套设计被选中的三条理由之一 —— 过度串行化不会报错，
      只会让系统悄悄退化成单线程，那种退化没有任何告警。
    """
    n = 8
    table = LockTable()

    results = _race(lambda i: table.acquire(_pid(i), [f"packages/mod-{i}/src"], at=AT), n)

    assert all(r.ok for r in results), [r.reason for r in results if not r.ok]
    assert len(table.to_ownership().locks) == n
    assert table.version == n


def test_release_then_reacquire_under_race() -> None:
    """持有者释放后，等待者必须能立刻拿到 —— 且仍然只有一个。"""
    table = LockTable()
    first = table.acquire(_pid(0), ["packages/shared"], at=AT)
    assert first.ok

    released = table.release(_pid(0))
    assert released == ("packages/shared",)

    results = _race(lambda i: table.acquire(_pid(i + 1), ["packages/shared/deep"], at=AT), 4)
    assert len([r for r in results if r.ok]) == 1
