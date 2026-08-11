"""会话身份与状态版本 —— 引擎与桌面端之间唯一的「我们在说同一件事」凭据。

════════════════════════════════════════════════════════════════
 为什么不能照抄 `_fake_engine.py`
════════════════════════════════════════════════════════════════

假引擎里 `revision` 是一个进程内变量，从 7 开始，每条命令 +1。作为传输层
的测试替身这没问题；作为真引擎会坏在两个地方：

1. **重启后回退。** 网关明确检查 `revision < self._state_revision` 并判
   `non_monotonic_state_revision`（`gateway.py:219`）。进程内计数器一重启
   就归零，于是网关会把重启后的第一条回执判为协议违规 —— 而真正出问题的
   是引擎，不是网关。

2. **runId 换了没人知道。** 桌面端拿着旧 runId 发命令，网关判 `run_mismatch`
   直接拒。这个拒绝是对的，但如果 runId 每次启动都变，用户看到的现象是
   「重启之后什么都点不动」，而日志里只有一个 run_mismatch。

所以这两样都必须**落盘**：同一个 `.codentum/` 就是同一次 run，重启是恢复，
不是新开一局。
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

__all__ = ["EngineSession"]

_SESSION_FILE = "engine-session.json"


@dataclass
class EngineSession:
    """`.codentum/engine-session.json` 的读写。

    ★ 只有两个字段，因为只有这两样是「跨进程必须一致」的。
      其余状态的真源是 packets/ 与 graph.json，不在这里重复一份 ——
      重复一份就会有两个真相，而它们迟早不一致。
    """

    state_dir: Path
    run_id: str
    revision: int

    @classmethod
    def load_or_create(cls, state_dir: Path | str) -> EngineSession:
        root = Path(state_dir)
        root.mkdir(parents=True, exist_ok=True)
        path = root / _SESSION_FILE
        if path.exists():
            try:
                raw = json.loads(path.read_text("utf-8"))
                run_id = raw["runId"]
                revision = raw["revision"]
            except (json.JSONDecodeError, KeyError, OSError):
                # ★ 文件坏了不能静默重建一个新 run —— 那会让桌面端手里的
                #   runId 突然失配，而现象是「按钮没反应」。宁可炸。
                raise ValueError(
                    f"{path} 无法解析。它记录着 runId 与 stateRevision，"
                    f"静默重建会让桌面端持有的 runId 失配。请人工确认后再删除。"
                ) from None
            if not isinstance(run_id, str) or not run_id:
                raise ValueError(f"{path} 里的 runId 不是非空字符串")
            if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
                raise ValueError(f"{path} 里的 revision 不是非负整数")
            return cls(state_dir=root, run_id=run_id, revision=revision)

        session = cls(state_dir=root, run_id=f"run-{uuid.uuid4()}", revision=0)
        session._persist()
        return session

    def bump(self) -> int:
        """状态确实变了才调用。返回新的版本号。

        ★ 调用点只有一处（EngineService._apply）。散落调用会让版本号
          与「状态是否真的变了」脱钩，而桌面端的乐观并发完全依赖这个对应关系。
        """
        self.revision += 1
        self._persist()
        return self.revision

    def _persist(self) -> None:
        path = self.state_dir / _SESSION_FILE
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(
                {"runId": self.run_id, "revision": self.revision},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        # os.replace 在 POSIX 与 Windows 上都是原子的；write_text 直接写目标文件
        # 会在崩溃时留下半个文件，而这个文件坏掉的后果见 load_or_create。
        os.replace(tmp, path)
