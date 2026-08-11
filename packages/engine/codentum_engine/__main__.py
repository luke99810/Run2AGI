"""stdio JSONL 引擎进程 —— `SidecarGateway` 启动的就是它。

启动方式（网关按 JSON argv 读，永远不走 shell）：

    CODENTUM_ENGINE_COMMAND_JSON='["python","-m","codentum_engine","--project-root","/path/to/repo"]'

════════════════════════════════════════════════════════════════
 ★ stdout 是协议通道，不是日志通道
════════════════════════════════════════════════════════════════

`JsonlEngineProxy` 按行读 stdout 并 `json.loads` 每一行。任何一句
`print()` 都会被当成一条协议响应，解析失败后 `_fail_all` 会把**所有**
在途请求一起判错 —— 现象是「引擎突然全线超时」，而真因只是某处打了个日志。

所以：
  - 日志一律走 stderr（`logging.basicConfig(stream=sys.stderr)`）
  - stderr 由代理持续排空，不会把管道堵死（假引擎里那 16384 行就是在测这个）
  - 这个文件里不出现任何 print
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, TextIO

from codentum_delivery.protocol import ProtocolViolation, error_response, parse_request, success_response

from .service import ENGINE_VERSION, EngineConfig, EngineService

logger = logging.getLogger("codentum_engine")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codentum-engine", description="Codentum stdio 引擎")
    parser.add_argument("--project-root", required=True, help="被开发的项目根目录（绝对路径）")
    parser.add_argument("--state-dir", default=None, help="状态目录，默认 <project-root>/.codentum")
    parser.add_argument("--model", default="qwen-coder-plus-1106")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--global-budget-cny", type=float, default=5.0)
    parser.add_argument("--packet-budget-cny", type=float, default=1.0)
    parser.add_argument("--api-key-env", default=None)
    parser.add_argument("--model-timeout-seconds", type=float, default=180.0)
    parser.add_argument(
        "--enforce-role-transitions",
        action="store_true",
        help="装载 RoleSpec 派生的 TransitionTable（见 EngineConfig 里的说明：现在打开会让 coder packet 停在 review）",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser


def _dispatch(service: EngineService, method: str, params: dict[str, Any]) -> Any:
    if method == "handshake":
        return service.handshake()
    if method == "command":
        command = params.get("command")
        if not isinstance(command, dict):
            raise ProtocolViolation("invalid_command", "params.command must be an object")
        return service.command(command)
    if method == "shutdown":
        return service.shutdown()
    raise ProtocolViolation("method_not_found", f"unsupported method: {method}")


def _force_utf8_streams() -> None:
    """把三条流钉死在 UTF-8 —— 这不是防御性代码，是一个跨平台缺陷的修复。

    ★ `JsonlEngineProxy` 是按 `encoding="utf-8"` 打开管道的。而 Windows 上
      Python 的 stdout/stderr 默认跟随本地代码页（本机是 GBK）。于是：

        引擎按 GBK 编码写出「做一个订阅费用管理器」
        代理按 UTF-8 解码，errors="replace"
        → 需求正文变成一串 U+FFFD，而**没有任何东西会报错**

      协议本身没坏（JSON 结构还在），坏的只有中文内容 —— 正好是最不容易
      在测试里被发现、也最容易在演示里被看见的那种。

      这已经是本项目第三次踩「一个概念在两侧各写一遍」：
      证据判据写两遍（门禁比兜底松）、EvidenceRef 路径分隔符两平台不一致，
      现在是流编码两侧不一致。
    """

    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _detach_protocol_stdin() -> TextIO:
    """把协议通道从 fd 0 上摘下来，并给 fd 0 挂上 devnull。

    ════════════════════════════════════════════════════════════
     ★ 这不是优化，是修一个会挂死进程、也会偷协议消息的缺陷
    ════════════════════════════════════════════════════════════

    引擎会派生子进程 —— worker 的 git 操作、命令行 runner，都是。
    子进程默认**继承父进程的 fd 0**，而引擎的 fd 0 正是桌面端发来的
    协议管道。后果有两个，一个吵一个静：

    **吵的那个（2026-08-11 首次真机跑通时实测到）**：
    `LocalWorkerRuntime.__init__` 会构造 `GitWorktreeManager`，后者在构造
    时跑 `git rev-parse --show-toplevel`。这个 git 子进程继承了协议管道
    作为 stdin，而引擎主线程正阻塞读同一个管道 —— 于是 git 卡住 **240 秒**
    才返回。实测日志：

        10:31:02  已取得状态锁，开始装配
        10:35:02  装配完成，开始 tick      ← 整整四分钟
        10:35:24  模型返回

    现象是「提交需求后四分钟没有任何反应」，而日志里一切正常、没有任何
    报错。查了三轮才定位到，因为「装配一个 runtime」看起来根本不像会
    做 I/O 的事。

    **静的那个（更严重）**：
    任何一个读 stdin 的子进程，都会**从协议通道里把桌面端发来的命令
    读走**。命令就此消失，桌面端等到超时，而引擎这边什么都没发生。
    这种缺陷不会报错、无法复现、也不会有测试变红。

    修法是把两者彻底分开：dup 出协议通道自己用，fd 0 换成 devnull。
    此后无论谁派生子进程、派生几层，子进程的 stdin 都是空的。

    ★ 为什么在入口层修：`worktree.py` 属于 `packages/harness/**`（B），
      给那边每个 `subprocess.run` 补 `stdin=DEVNULL` 既越界，也只能堵住
      当下这几处 —— 下一个新增的子进程调用又会漏。fd 0 只有一个，
      在进程入口一次性换掉，才是覆盖全部子进程的那个位置。
    """

    protocol_fd = os.dup(0)
    devnull = os.open(os.devnull, os.O_RDONLY)
    try:
        os.dup2(devnull, 0)
    finally:
        os.close(devnull)
    return open(protocol_fd, encoding="utf-8", errors="replace", newline="")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    _force_utf8_streams()
    protocol_in = _detach_protocol_stdin()

    logging.basicConfig(
        stream=sys.stderr,
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    project_root = Path(args.project_root).resolve()
    if not project_root.is_dir():
        # ★ 在握手之前就退出，而不是握手时报 connected=false：
        #   路径打错是配置问题，应该让启动它的人立刻看见，
        #   而不是变成桌面端上一句「引擎不可用」。
        logger.error("project-root 不是目录：%s", project_root)
        return 2

    service = EngineService(
        EngineConfig(
            project_root=project_root,
            state_dir=Path(args.state_dir).resolve() if args.state_dir else None,
            model=args.model,
            effort=args.effort,
            global_budget_cny=args.global_budget_cny,
            packet_budget_cny=args.packet_budget_cny,
            api_key_env=args.api_key_env,
            model_timeout_seconds=args.model_timeout_seconds,
            enforce_role_transitions=args.enforce_role_transitions,
        )
    )
    logger.info("%s 就绪：run=%s revision=%d", ENGINE_VERSION, service.run_id, service.revision)

    out = sys.stdout
    for line in protocol_in:
        if not line.strip():
            continue
        try:
            decoded = json.loads(line)
        except json.JSONDecodeError:
            # ★ 无法解析的行连 id 都取不到，回不了对应的响应。丢掉并记日志是
            #   唯一能做的事 —— 代理那边会以超时收场，那是准确的现象。
            logger.warning("收到无法解析的 JSON 行，已丢弃")
            continue

        try:
            request = parse_request(decoded)
        except ProtocolViolation as exc:
            request_id = decoded.get("id") if isinstance(decoded, dict) else None
            if isinstance(request_id, str) and request_id:
                _emit(out, error_response(request_id, exc.code, str(exc)))
            else:
                logger.warning("请求缺少可用的 id，无法回错：%s", exc.code)
            continue

        try:
            result = _dispatch(service, request.method, request.params)
            _emit(out, success_response(request.request_id, result))
        except ProtocolViolation as exc:
            _emit(out, error_response(request.request_id, exc.code, str(exc)))
        except Exception:
            # ★ 异常细节只进 stderr。回给上游的消息不带内部信息 ——
            #   同 `SidecarGateway.dispatch` 的做法：错误响应会一路走到
            #   桌面端，把路径、环境变量、模型报文顺着它带出去是泄露。
            logger.exception("处理 %s 失败", request.method)
            _emit(out, error_response(request.request_id, "internal_error", "engine request failed"))

        if request.method == "shutdown":
            break

    protocol_in.close()
    logger.info("协议通道关闭，引擎退出")
    return 0


def _emit(stream: Any, payload: dict[str, Any]) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    stream.flush()


if __name__ == "__main__":
    raise SystemExit(main())
