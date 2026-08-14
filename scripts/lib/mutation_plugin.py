"""变异插件：在 pytest 启动最早期，把某一条判据替换成「永远放行」。

════════════════════════════════════════════════════════════════
 ★ 为什么变异体必须在结构上与原判据不可区分
════════════════════════════════════════════════════════════════

朴素做法是「把这条规则从 DEFAULT_RULES 里删掉」。那样测出来的数是**虚高**的：
仓库里只要有一条断言 `len(DEFAULT_RULES) == 8`、或者比对规则名字列表的测试，
删掉任何一条规则它都会红 —— 但这不叫「这条判据被守着」，
这叫「判据的**数量**被守着」。两者差得很远。

★ 所以变异体保持：同一个函数对象、同一个 __name__、元组里同一个位置，
  **只有行为变成永远放行**。这样结构断言一条都杀不死它，
  能让测试变红的只可能是**行为断言** —— 那才是我们要数的东西。

════════════════════════════════════════════════════════════════
 ★ 两类判据用两种手法，因为它们的「放行」不是同一个值
════════════════════════════════════════════════════════════════

| 判据 | 放行的表示 | 变异手法 |
|---|---|---|
| admission 规则 | 返回 `None` | 置换 `__code__` |
| gate 门禁 | 返回 `passed=True` 的 GateVerdict | 在注册处包一层 |

规则为什么不能用「重新赋值模块属性」：
`checker.py` 里 `rules: Sequence[RuleFn] = field(default=DEFAULT_RULES)`
是**类定义时求值**的 dataclass 默认值 —— 等测试跑起来再改
`rules.DEFAULT_RULES`，那个元组早就被捕获走了，改了也没用。
而置换 `__code__` 是原地改函数对象本身，**所有已经持有该引用的地方同时生效**。

门禁为什么不用同样手法：`__code__` 置换只能让它返回常量，
而返回一个 gate_id 是假的 GateVerdict 会被结构断言杀死（假杀死）。
改成在 `GateRunner.register` —— 唯一的注册收口 —— 包一层强制 passed=True，
gate_id / detail / evidence 全部保留原值，结构上依旧不可区分。
"""

from __future__ import annotations

import os
from typing import Any


def _always_none(*_args: Any, **_kwargs: Any) -> None:
    """规则变异体的躯壳：无论输入是什么都判定「无违规」。"""

    return None


def _mutate_rule(name: str) -> None:
    from codentum_control_plane.admission import rules as rules_mod

    target = getattr(rules_mod, name, None)
    if target is None:
        raise SystemExit(f"[mutation] 没有这条规则：{name}")

    # ★ 置换 __code__ 而不是重新绑定名字 —— 后者影响不到已经装进
    #   DEFAULT_RULES 元组、以及被 dataclass 默认值捕获的那个引用。
    target.__code__ = _always_none.__code__


def _inject_noop_rule() -> None:
    """正对照：往规则集里塞一条本来就什么都不管的规则。

    ★ 它**必须存活**。这一条不是为了发现问题，是为了证明这个脚本
      **有能力报出「存活」** —— 否则「全部被杀死」这个结论可能只是
      管道坏了（比如插件根本没加载，每轮跑的都是未变异的代码）。
      一个只会输出同一个答案的检查，和不做检查是等价的。
    """

    from codentum_control_plane.admission import rules as rules_mod

    def _canary(*_args: Any, **_kwargs: Any) -> None:
        return None

    _canary.__name__ = "check_canary_noop"
    rules_mod.DEFAULT_RULES = (*rules_mod.DEFAULT_RULES, _canary)


def _mutate_gate(gate_id: str) -> None:
    from codentum_control_plane.gates.runner import GateRunner

    original_register = GateRunner.register

    def patched_register(self: Any, registered_id: str, fn: Any, *args: Any, **kwargs: Any) -> Any:
        if registered_id == gate_id:
            def forced(*a: Any, **kw: Any) -> Any:
                verdict = fn(*a, **kw)
                # ★ 只翻转结论，gate_id / detail / evidence_refs 原样保留 ——
                #   任何比对这些字段的断言都杀不死它，只有比对 passed 的能。
                return type(verdict)(
                    passed=True,
                    gate_id=verdict.gate_id,
                    detail=verdict.detail,
                    evidence_refs=verdict.evidence_refs,
                )

            forced.__name__ = getattr(fn, "__name__", registered_id)
            fn = forced
        return original_register(self, registered_id, fn, *args, **kwargs)

    GateRunner.register = patched_register  # type: ignore[assignment]


def pytest_configure(config: object) -> None:
    """pytest 启动钩子。用 `-p` 加载，早于 conftest 与任何测试导入。"""

    spec = os.environ.get("CODENTUM_MUTANT", "").strip()
    if not spec or spec == "none":
        return  # 基线轮：不变异，用来确认套件本来就是绿的

    kind, _, name = spec.partition(":")
    if kind == "rule":
        _mutate_rule(name)
    elif kind == "gate":
        _mutate_gate(name)
    elif kind == "canary":
        _inject_noop_rule()
    else:
        raise SystemExit(f"[mutation] 无法识别的变异目标：{spec!r}")
