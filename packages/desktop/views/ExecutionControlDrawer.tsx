import type { FormEvent } from "react";
import { useEffect, useState } from "react";
import type { WorkPacket } from "@codentum/contracts";
import {
  applyExecutionCommand,
  getExecutionModuleStatus,
  parseModuleAddCommand,
  selectExecutionModule,
} from "./execution-control-model";
import type {
  ExecutionCommand,
  ExecutionMode,
  ExecutionModuleStatus,
  ExecutionSession,
} from "./execution-control-model";

interface ExecutionControlDrawerProps {
  readonly onClose: () => void;
  readonly onSessionChange: (session: ExecutionSession) => void;
  readonly packet: WorkPacket;
  readonly session: ExecutionSession;
}

const MODE_LABELS: Readonly<Record<ExecutionMode, string>> = {
  auto: "默认自动执行",
  paused: "完成当前步骤后暂停",
  stopped: "已停止",
};

const MODULE_STATUS_LABELS: Readonly<Record<ExecutionModuleStatus, string>> = {
  done: "已完成",
  current: "当前模块",
  upcoming: "待执行",
};

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

export function ExecutionControlDrawer({
  onClose,
  onSessionChange,
  packet,
  session,
}: ExecutionControlDrawerProps): React.JSX.Element {
  const [retainMemory, setRetainMemory] = useState(session.retainMemory);
  const [promptText, setPromptText] = useState("");
  const [moduleCommand, setModuleCommand] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    setRetainMemory(session.retainMemory);
  }, [session.retainMemory]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose]);

  const selectedModule = session.modules.find((module) => module.id === session.selectedModuleId);
  if (selectedModule === undefined) {
    throw new RangeError(`找不到所选执行模块：${session.selectedModuleId}`);
  }
  const selectedPrompts = session.promptNotes[selectedModule.id] ?? [];

  const applyCommand = (command: ExecutionCommand): boolean => {
    try {
      onSessionChange(applyExecutionCommand(session, command));
      setFormError(null);
      return true;
    } catch (reason: unknown) {
      setFormError(errorMessage(reason));
      return false;
    }
  };

  const handlePromptSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (applyCommand({ type: "append_prompt", text: promptText })) {
      setPromptText("");
    }
  };

  const handleModuleSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    try {
      const command = parseModuleAddCommand(moduleCommand);
      if (applyCommand(command)) {
        setModuleCommand("");
      }
    } catch (reason: unknown) {
      setFormError(errorMessage(reason));
    }
  };

  return (
    <div
      className="execution-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
    >
      <aside
        aria-labelledby="execution-drawer-title"
        aria-modal="true"
        className="execution-drawer"
        role="dialog"
      >
        <header className="execution-drawer-header">
          <div>
            <span className="section-tag">任务执行</span>
            <h2 id="execution-drawer-title">{packet.id}</h2>
            <p>{packet.role} · 当前状态 {packet.state}</p>
          </div>
          <button aria-label="关闭执行控制" className="drawer-close" onClick={onClose} type="button">×</button>
        </header>

        <div className="control-tip">
          <strong>点击模块即可调整执行方式</strong>
          <span>未选择操作时，任务会按默认顺序继续。</span>
        </div>

        <div className={`execution-mode mode-${session.mode}`}>
          <span className="execution-mode-dot" />
          <div>
            <small>执行方式</small>
            <strong>{MODE_LABELS[session.mode]}</strong>
          </div>
        </div>

        <section className="execution-section" aria-labelledby="module-heading">
          <div className="execution-section-heading">
            <div>
              <span>01</span>
              <div>
                <h3 id="module-heading">选择执行模块</h3>
                <p>不设置操作时，Agent 按默认顺序继续。</p>
              </div>
            </div>
          </div>

          <div className="execution-timeline">
            {session.modules.map((module, index) => {
              const status = getExecutionModuleStatus(session, module.id);
              const promptCount = session.promptNotes[module.id]?.length ?? 0;
              return (
                <button
                  aria-pressed={session.selectedModuleId === module.id}
                  className={`execution-module status-${status}${session.selectedModuleId === module.id ? " selected" : ""}`}
                  key={module.id}
                  onClick={() => {
                    onSessionChange(selectExecutionModule(session, module.id));
                    setFormError(null);
                  }}
                  type="button"
                >
                  <span className="module-index">{String(index + 1).padStart(2, "0")}</span>
                  <span className="module-copy">
                    <strong>{module.label}</strong>
                    <small>{module.description}</small>
                  </span>
                  <span className="module-meta">
                    {module.custom ? <i>新增</i> : null}
                    {promptCount > 0 ? <i>{promptCount} Prompt</i> : null}
                    <em>{MODULE_STATUS_LABELS[status]}</em>
                  </span>
                </button>
              );
            })}
          </div>
        </section>

        <section className="execution-section" aria-labelledby="action-heading">
          <div className="execution-section-heading">
            <div>
              <span>02</span>
              <div>
                <h3 id="action-heading">干预所选模块</h3>
                <p>当前选择：{selectedModule.label}</p>
              </div>
            </div>
          </div>

          <div className="execution-actions">
            <button
              className="control-button pause"
              disabled={session.mode === "paused"}
              onClick={() => { applyCommand({ type: "pause" }); }}
              type="button"
            >
              完成当前步骤后暂停
            </button>
            <button
              className="control-button resume"
              disabled={session.mode === "auto"}
              onClick={() => { applyCommand({ type: "resume" }); }}
              type="button"
            >
              按默认继续
            </button>
            <button
              className="control-button back"
              onClick={() => { applyCommand({ type: "back" }); }}
              type="button"
            >
              返回上一步
            </button>
          </div>

          <div className="stop-control">
            <label>
              <input
                checked={retainMemory}
                onChange={(event) => { setRetainMemory(event.currentTarget.checked); }}
                type="checkbox"
              />
              停止时保留本次记忆
            </label>
            <button
              onClick={() => { applyCommand({ type: "stop", retainMemory }); }}
              type="button"
            >
              停止任务
            </button>
          </div>
        </section>

        <section className="execution-section" aria-labelledby="prompt-heading">
          <div className="execution-section-heading">
            <div>
              <span>03</span>
              <div>
                <h3 id="prompt-heading">追加 Prompt</h3>
                <p>追加内容绑定到所选模块，并保留操作记录。</p>
              </div>
            </div>
          </div>

          <form className="prompt-form" onSubmit={handlePromptSubmit}>
            <textarea
              aria-label="追加 Prompt"
              onChange={(event) => { setPromptText(event.currentTarget.value); }}
              placeholder="例如：先检查并发冲突，再修改实现。"
              rows={3}
              value={promptText}
            />
            <button type="submit">加入 Prompt</button>
          </form>
          {selectedPrompts.length === 0 ? null : (
            <ol className="prompt-notes">
              {selectedPrompts.map((note, index) => <li key={`${note}-${index}`}>{note}</li>)}
            </ol>
          )}
        </section>

        <section className="execution-section" aria-labelledby="insert-heading">
          <div className="execution-section-heading">
            <div>
              <span>04</span>
              <div>
                <h3 id="insert-heading">用指令增加模块</h3>
                <p>新模块插入在当前选择之后。</p>
              </div>
            </div>
          </div>

          <form className="module-command-form" onSubmit={handleModuleSubmit}>
            <input
              aria-label="新增执行模块指令"
              onChange={(event) => { setModuleCommand(event.currentTarget.value); }}
              placeholder="/module add 安全复核"
              value={moduleCommand}
            />
            <button type="submit">执行指令</button>
          </form>
        </section>

        {formError === null ? null : <div className="control-form-error" role="alert">{formError}</div>}

        {session.lastReceipt === null ? null : (
          <div className="execution-receipt" role="status">
            <span>操作已更新</span>
            <strong>{session.lastReceipt.message}</strong>
          </div>
        )}
      </aside>
    </div>
  );
}
