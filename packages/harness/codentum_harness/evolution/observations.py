"""L0 观察提取：把一次执行的 transcript 变成可晋级的经验条目。

════════════════════════════════════════════════════════════════
 ★ 为什么是确定性提取，而不是让模型总结自己这次学到了什么
════════════════════════════════════════════════════════════════

「跑完让模型写一段复盘」是最容易想到的做法，也是错的：
**那等于让执行者给自己写教材** —— 它总结出来的东西没有任何独立的东西检验，
而下一次它会照着这份自己写的教材去做。错误会被自己确认成经验。

确定性提取的东西不一样：它是**从证据里抽出来的事实**，
每一条都能指回 transcript 的具体位置，因此**可以被证伪** ——
有人可以去看那一行，说「这条不成立」。

════════════════════════════════════════════════════════════════
 ★ 只写事实，不写结论
════════════════════════════════════════════════════════════════

| | 例子 | 为什么 |
|---|---|---|
| ❌ 结论 | 「用 pytest 比较好」 | 无证据、不可证伪，而且它是**判据走私进执行者上下文** |
| ✅ 事实 | 「run_tests 在 cwd=workspace/ 下返回 ImportError: No module named 'src'」 | 带证据引用，可以去核对，也可以被推翻 |

写回结论的危险在于：执行者下次会照着那条结论做，
而**没有任何东西在检验这条结论是否还成立**。事实不会有这个问题 ——
事实过期了，下次的证据会和它对不上。

════════════════════════════════════════════════════════════════
 ★ 指纹：晋级靠它，而它必须归一化到「同一类失败」
════════════════════════════════════════════════════════════════

L0 → L1 的条件是**同一类失败在 ≥2 个不同 packet 里复现**（契约里
L1 的定义就是「重复出现」）。所以要判断「是不是同一类」。

归一化剥掉：绝对路径、行号、十六进制地址、临时目录名、纯数字。
**保留**：工具名、异常类型、错误骨架。

★ 归一化的尺度是有代价的，两个方向都会错：
  - 太松 → 不同的失败被并成一类，晋级上去的经验是错的
  - 太紧 → 同一类失败永远凑不满 2 次，什么都晋级不了（更常见）
  这里选择**保留异常类型但剥掉具体标识符**，并且把原文留在 text 里 ——
  指纹用来聚类，人看的是原文。聚错了，看原文能发现。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

__all__ = ["Observation", "extract_observations", "fingerprint_failure"]


_NOISE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"0x[0-9a-fA-F]+", "<addr>"),  # 内存地址
    (r"[A-Za-z]:\\[^\s'\"]+", "<path>"),  # Windows 绝对路径
    (r"/(?:tmp|var|home|Users)/[^\s'\"]+", "<path>"),  # POSIX 绝对路径
    (r"line \d+", "line <n>"),  # 行号
    (r"\b\d+\.\d+s\b", "<dur>"),  # 耗时
    (r"\b\d{3,}\b", "<num>"),  # 长数字（PID、端口、时间戳）
)

_MAX_TEXT = 400
"""单条观察的正文上限。★ 观察是给上下文用的，不是日志归档 —— 存全文会挤掉真正要读的东西。"""


@dataclass(frozen=True, slots=True)
class Observation:
    """一条 L0 观察。

    ★ `evidence_refs` 为空的观察**不允许写入** —— 这是 I6（证据不变量）
      在记忆层的落点。没有证据的「经验」和模型的臆想不可区分，
      而一旦写进索引，它下次就会以「系统记忆」的身份出现在上下文里，
      比模型当场瞎猜更难被发现。
    """

    fingerprint: str
    text: str
    evidence_refs: tuple[str, ...]
    tool: str

    def __post_init__(self) -> None:
        if not self.evidence_refs:
            raise ValueError(
                f"观察缺少证据引用（tool={self.tool!r}）。"
                "没有证据的经验不可证伪，不允许进入记忆索引。"
            )


def fingerprint_failure(tool: str, content: str) -> str:
    """把一次失败归一化成可跨 packet 比对的指纹。"""

    skeleton = content.strip()
    for pattern, replacement in _NOISE_PATTERNS:
        skeleton = re.sub(pattern, replacement, skeleton)
    # ★ 只取前两行：异常类型与首条消息足以定类，
    #   往后是调用栈 —— 同一类失败在不同 packet 里栈必然不同，
    #   带上它就永远聚不到一起。
    skeleton = "\n".join(skeleton.splitlines()[:2])
    digest = hashlib.sha256(f"{tool}\n{skeleton}".encode()).hexdigest()
    return f"obs-{digest[:16]}"


def extract_observations(
    transcript: list[dict[str, Any]],
    *,
    packet_id: str,
    transcript_path: str = "tool_transcript.json",
) -> list[Observation]:
    """从一次执行的 transcript 里抽出 L0 观察。

    ★ 只抽**失败**。成功的调用不构成经验 —— 「这次成功了」推不出
      「下次这么做也会成功」，把它写进记忆只是在稀释信噪比。
      失败不一样：它是一条**确实存在的约束**撞出来的印子。
    """

    observations: list[Observation] = []
    seen: set[str] = set()

    for position, entry in enumerate(transcript):
        if entry.get("ok", True):
            continue
        tool = str(entry.get("tool", "<unknown>"))
        content = str(entry.get("content", "")).strip()
        if not content:
            continue  # 没有错误正文 → 提不出可证伪的事实

        fingerprint = fingerprint_failure(tool, content)
        if fingerprint in seen:
            continue  # ★ 同一次执行内重复 N 次不算 N 条证据，只算一条
        seen.add(fingerprint)

        text = content if len(content) <= _MAX_TEXT else content[:_MAX_TEXT] + "…"
        observations.append(
            Observation(
                fingerprint=fingerprint,
                text=f"工具 {tool} 失败：{text}",
                # ★ 证据指到 transcript 里的具体下标 —— 可以去核对那一条
                evidence_refs=(f"{packet_id}:{transcript_path}#{position}",),
                tool=tool,
            )
        )

    return observations
