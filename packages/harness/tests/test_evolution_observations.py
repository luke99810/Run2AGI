"""L0 观察提取的判据。

★ 这组测试守的是「什么配被记住」。记忆层的错误比模型当场瞎猜更难发现 ——
  模型瞎猜只影响这一次，写进索引的错误经验会以「系统记忆」的身份
  出现在**以后每一次**的上下文里。
"""

from __future__ import annotations

import pytest

from codentum_harness.evolution import (
    Observation,
    extract_observations,
    fingerprint_failure,
)


def _entry(tool: str, ok: bool, content: str) -> dict[str, object]:
    return {"tool": tool, "input": {}, "ok": ok, "content": content}


# ══════════════════════════════════════════════════════════════
#  什么配被记住
# ══════════════════════════════════════════════════════════════


def test_successful_calls_produce_no_observations() -> None:
    """★ 「这次成功了」推不出「下次这么做也会成功」。

    把成功写进记忆只是在稀释信噪比 —— 真正要读的那几条会被挤掉。
    """

    transcript = [_entry("write_file", True, "已写入 12 行"), _entry("run_tests", True, "3 passed")]
    assert extract_observations(transcript, packet_id="p-1") == []


def test_failure_becomes_an_observation_with_evidence() -> None:
    transcript = [_entry("run_tests", False, "ImportError: No module named 'src'")]
    (obs,) = extract_observations(transcript, packet_id="p-1")

    assert obs.tool == "run_tests"
    assert "ImportError" in obs.text
    assert obs.evidence_refs == ("p-1:tool_transcript.json#0",), "证据必须指回 transcript 的具体下标"


def test_observation_without_evidence_is_rejected_at_construction() -> None:
    """★ I6 在记忆层的落点：没有证据的「经验」不允许存在。

    它和模型的臆想在事后是**不可区分**的 —— 谁也没法回去核对它。
    所以拦在构造处，而不是拦在写入处：拦在写入处的话，
    代码里仍然可以造出一个无证据的 Observation 传来传去。
    """

    with pytest.raises(ValueError, match="证据"):
        Observation(fingerprint="obs-x", text="随便什么", evidence_refs=(), tool="run_tests")


def test_empty_error_body_yields_nothing() -> None:
    """失败但没有错误正文 → 抽不出任何可证伪的事实，不该硬造一条。"""

    assert extract_observations([_entry("run_build", False, "   ")], packet_id="p-1") == []


# ══════════════════════════════════════════════════════════════
#  指纹：晋级的基础，两个方向都会错
# ══════════════════════════════════════════════════════════════


def test_same_failure_class_fingerprints_alike_across_machines() -> None:
    """★ 太紧的指纹 = 同一类失败永远凑不满 2 次 = 什么都晋级不了。

    这是更常见的失败方向，而且它是**静默**的：进化层看起来在运行，
    只是「恰好还没有经验够格晋级」—— 和坏掉了完全一样。
    """

    a = fingerprint_failure("run_tests", "File \"D:\\work\\a\\t.py\", line 12\nImportError: No module named 'src'")
    b = fingerprint_failure("run_tests", "File \"/home/ci/b/t.py\", line 87\nImportError: No module named 'src'")
    assert a == b, "同一类失败换了机器和行号就聚不到一起，晋级永远触发不了"


def test_different_failure_classes_stay_apart() -> None:
    """★ 太松的指纹 = 不同失败被并成一类 = 晋级上去的经验是错的。"""

    a = fingerprint_failure("run_tests", "ImportError: No module named 'src'")
    b = fingerprint_failure("run_tests", "AssertionError: expected 3 got 4")
    assert a != b


def test_same_error_from_different_tools_stays_apart() -> None:
    """工具不同 = 约束不同，哪怕错误文本一样。"""

    assert fingerprint_failure("run_tests", "timeout") != fingerprint_failure("run_build", "timeout")


def test_repeats_within_one_run_count_once() -> None:
    """★ 同一次执行里撞同一堵墙 5 次，是 1 条证据不是 5 条。

    否则一个陷在重试循环里的执行会单独把某条经验顶上晋级线 ——
    而「重复出现」的本意是**跨 packet 复现**，不是同一次里打转。
    """

    transcript = [_entry("run_tests", False, "ImportError: No module named 'src'")] * 5
    assert len(extract_observations(transcript, packet_id="p-1")) == 1
