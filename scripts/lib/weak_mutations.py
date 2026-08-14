"""弱变异算子：在 AST 层改判据的逻辑，而不是整条摘掉。

════════════════════════════════════════════════════════════════
 ★ 强变异 0% 存活，为什么还要做弱变异
════════════════════════════════════════════════════════════════

强变异（`__code__` 换成「永远放行」）测的是「这条判据被**整条摘掉**
有没有人发现」。12/12 全被杀死，说明每条判据都至少有一条测试碰过它。

但真实缺陷不长那样。真实缺陷是：

    `if packet.budget.limitCny <= 0:`   写成   `< 0`
    `if a and b:`                       写成   `a or b`
    `if not paths:`                     写成   `if paths:`

判据**还在**、还会被调用、还会返回 Violation，只是**边界挪了一格**。
一条只覆盖了「明显违规」的测试照样是绿的 —— 它从来没测过边界那一格。

★ 所以两个数测的是两件事：
  强变异存活率 = 有没有人**碰过**这条判据
  弱变异存活率 = 有没有人**测准**这条判据的边界

后者才是「判据可信」这句话真正需要的证据。

════════════════════════════════════════════════════════════════
 ★ 等价变异体：这个数天生带噪声，不能当成缺陷清单
════════════════════════════════════════════════════════════════

有些变异在语义上和原代码等价（比如改的是一个永远走不到的分支里的比较），
它们必然存活，但**不是缺口**。这是变异测试的固有问题，无解，只能标注。

所以存活者报出来是「**需要人看一眼**」，不是「确定的缺陷」。
把等价变异体算成缺陷，和把它们算成通过，是同一种不诚实的两个方向。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any, Callable

__all__ = ["MutationSite", "apply_mutation", "enumerate_sites"]

# 比较符换向：都是「差一格」的改法，不是随便换成别的
_CMP_SWAP: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
}

_CMP_NAME: dict[type[ast.cmpop], str] = {
    ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=",
    ast.Eq: "==", ast.NotEq: "!=", ast.In: "in", ast.NotIn: "not in",
}


@dataclass(frozen=True, slots=True)
class MutationSite:
    index: int
    """在**访问顺序**中的序号。★ 必须确定性 —— 驱动脚本按序号点名，
    插件按同一序号定位。两边顺序不一致会变异到错误的位置，
    而且报出来的描述会和实际改的地方对不上。"""

    kind: str
    """COR 比较符 / LOR 逻辑符 / NOT 去否定 / CONST 常量差一"""

    detail: str
    """给人看的：`第 101 行 <= → <`"""


class _Walker(ast.NodeVisitor):
    """按确定性顺序收集可变异点。"""

    def __init__(self, line_offset: int = 0) -> None:
        self.sites: list[MutationSite] = []
        # ★ 解析的是 dedent 过的**函数片段**，节点行号从 1 起算。
        #   不加回函数在文件里的起始行，报出来的「第 3 行」谁也定位不到 ——
        #   一份定位不了的缺口清单，和没有清单差别不大。
        self._offset = line_offset

    def _add(self, node: ast.AST, kind: str, detail: str) -> None:
        self.sites.append(MutationSite(len(self.sites), kind, detail))

    def visit_Compare(self, node: ast.Compare) -> None:
        if len(node.ops) == 1 and type(node.ops[0]) in _CMP_SWAP:
            op = type(node.ops[0])
            self._add(
                node, "COR",
                f"L{node.lineno + self._offset} {_CMP_NAME[op]} → {_CMP_NAME[_CMP_SWAP[op]]}",
            )
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        flipped = "or" if isinstance(node.op, ast.And) else "and"
        original = "and" if isinstance(node.op, ast.And) else "or"
        self._add(node, "LOR", f"L{node.lineno + self._offset} {original} → {flipped}")
        self.generic_visit(node)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> None:
        if isinstance(node.op, ast.Not):
            self._add(node, "NOT", f"L{node.lineno + self._offset} 去掉 not")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        # ★ 只动 int，且排除 bool —— isinstance(True, int) 是 True，
        #   把 True 改成 2 不是「差一格」，是改成了别的东西。
        if isinstance(node.value, int) and not isinstance(node.value, bool):
            self._add(node, "CONST", f"L{node.lineno + self._offset} {node.value} → {node.value + 1}")
        self.generic_visit(node)


class _Mutator(ast.NodeTransformer):
    """只改指定序号的那一个点。访问顺序必须与 `_Walker` 完全一致。"""

    def __init__(self, target: int) -> None:
        self._target = target
        self._seen = 0
        self.applied = False

    def _hit(self) -> bool:
        hit = self._seen == self._target
        self._seen += 1
        if hit:
            self.applied = True
        return hit

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        if len(node.ops) == 1 and type(node.ops[0]) in _CMP_SWAP:
            if self._hit():
                node.ops = [_CMP_SWAP[type(node.ops[0])]()]
        self.generic_visit(node)
        return node

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.AST:
        if self._hit():
            node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        self.generic_visit(node)
        return node

    def visit_UnaryOp(self, node: ast.UnaryOp) -> ast.AST:
        if isinstance(node.op, ast.Not):
            if self._hit():
                self.generic_visit(node)
                return node.operand  # 去掉 not
        self.generic_visit(node)
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        if isinstance(node.value, int) and not isinstance(node.value, bool):
            if self._hit():
                return ast.Constant(value=node.value + 1)
        return node


def _function_ast(func: Callable[..., Any]) -> tuple[ast.Module, int]:
    """返回 (函数片段的 AST, 该片段在源文件里的起始行 - 1)。"""

    import inspect
    import textwrap

    lines, start = inspect.getsourcelines(func)
    return ast.parse(textwrap.dedent("".join(lines))), start - 1


def enumerate_sites(func: Callable[..., Any]) -> list[MutationSite]:
    """列出一个判据函数里所有可变异的点。"""

    tree, offset = _function_ast(func)
    walker = _Walker(offset)
    walker.visit(tree)
    return walker.sites


def apply_mutation(func: Callable[..., Any], index: int) -> str:
    """把第 `index` 个变异点应用到 `func` 上（原地换 __code__）。

    ★ 依旧用 `__code__` 置换而不是重新绑定名字 —— 判据已经被装进
      `DEFAULT_RULES` 元组、并被 dataclass 默认值捕获，
      重新绑定模块属性影响不到那些引用。
    """

    tree, _ = _function_ast(func)
    mutator = _Mutator(index)
    tree = mutator.visit(tree)
    if not mutator.applied:
        raise SystemExit(f"[weak] {func.__name__} 没有第 {index} 个变异点")
    ast.fix_missing_locations(tree)

    # ★ 带上 annotations 的 future 标志：原模块有 `from __future__ import
    #   annotations`，签名里的注解在那里是字符串。不带这个标志重编译，
    #   注解会在 def 时被真的求值 —— 一旦有前向引用就炸，
    #   而炸出来的错会被误读成「这个变异体杀死了测试」。
    import __future__  # noqa: PLC0415  （`from __future__ import` 只能在文件开头）

    namespace: dict[str, Any] = dict(func.__globals__)
    exec(  # noqa: S102
        compile(tree, f"<weak-mutant:{func.__name__}>", "exec",
                flags=__future__.annotations.compiler_flag),
        namespace,
    )
    func.__code__ = namespace[func.__name__].__code__
    return func.__name__
