"""进化平面：把执行留下的证据变成可晋级的经验。

★ 与 `memory_index` 的分工：
  `memory_index` 是**存储与检索**（记忆放哪、怎么取回来）；
  这里是**产生与晋级**（哪些东西配被记住、凭什么升一级）。

  契约 `interfaces.py` 里那条晋升链 ——
  L0 一次观察 → L1 重复出现 → L2 归纳成假说 → L3 过证伪门 → L4 固化为规则
  —— 存储层早就实现了 `promote()`，但在真实执行路径上**从未被调用过**。
  这个包补的就是那一段：**谁来产生 L0，凭什么往上晋级。**
"""

from .observations import Observation, extract_observations, fingerprint_failure
from .recall import experience_context_candidates_now

__all__ = [
    "Observation",
    "experience_context_candidates_now",
    "extract_observations",
    "fingerprint_failure",
]
