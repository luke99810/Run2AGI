import type { ChangeEvent } from "react";
import { useEffect, useState } from "react";
import { useStateSnapshot } from "@desktop/renderer/src/useStateSnapshot";
import { IndustryWorkspace } from "./IndustryWorkspace";
import { KanbanBoard } from "./KanbanBoard";
import { OperationsDashboard } from "./OperationsDashboard";

type ViewId = "industry" | "operations" | "kanban";

const navigationItems = [
  { id: "industry", label: "方案中心", view: "industry" },
  { id: "operations", label: "Agent 状态", view: "operations" },
  { id: "kanban", label: "任务看板", view: "kanban" },
] as const satisfies readonly {
  readonly id: string;
  readonly label: string;
  readonly view: ViewId;
}[];

export function HomeView(): React.JSX.Element {
  const [activeView, setActiveView] = useState<ViewId>("industry");
  const {
    sources,
    selectedSourceId,
    selectSource,
    snapshot,
    loading,
    error,
  } = useStateSnapshot();

  const handleSourceChange = (event: ChangeEvent<HTMLSelectElement>): void => {
    selectSource(event.currentTarget.value);
  };

  useEffect(() => {
    const resetScroll = (): void => {
      document.querySelector<HTMLElement>(".product-app-main")?.scrollTo({
        top: 0,
        behavior: "auto",
      });
    };

    resetScroll();
    const frame = window.requestAnimationFrame(resetScroll);
    const timer = window.setTimeout(resetScroll, 250);

    return () => {
      window.cancelAnimationFrame(frame);
      window.clearTimeout(timer);
    };
  }, [activeView]);

  return (
    <div className="product-app-shell">
      <header className="product-app-header">
        <button
          className="product-brand"
          onClick={() => { setActiveView("industry"); }}
          type="button"
        >
          <span className="product-brand-mark" aria-hidden="true">C</span>
          <span>
            <strong>Codentum</strong>
            <small>AgentTeams 自适应平台</small>
          </span>
        </button>

        <nav className="product-app-nav" aria-label="主导航">
          {navigationItems.map((item) => (
            <button
              aria-current={activeView === item.view ? "page" : undefined}
              className={activeView === item.view ? "active" : ""}
              key={item.id}
              onClick={() => { setActiveView(item.view); }}
              type="button"
            >
              {item.label}
            </button>
          ))}
        </nav>

        <div className="product-header-actions">
          {activeView !== "industry" ? (
            <label className="source-picker product-source-picker">
              <span>运行场景</span>
              <select
                aria-label="选择运行场景"
                disabled={sources.length === 0}
                onChange={handleSourceChange}
                value={selectedSourceId}
              >
                {sources.map((source) => (
                  <option key={source.id} value={source.id}>{source.label}</option>
                ))}
              </select>
            </label>
          ) : null}
          <div className="product-runtime-badge">
            <span className={error === null ? "status-dot" : "status-dot error"} />
            {error === null ? "系统在线" : "加载失败"}
          </div>
        </div>
      </header>

      <div className={`product-app-main view-${activeView}`}>
        {error === null ? null : <div className="error-banner" role="alert">{error}</div>}

        {activeView === "industry" ? (
          <IndustryWorkspace />
        ) : activeView === "operations" ? (
          <OperationsDashboard loading={loading} snapshot={snapshot} />
        ) : (
          <section className="kanban-page" aria-labelledby="kanban-page-title">
            <header className="kanban-page-header">
              <div>
                <p className="eyebrow">运行任务</p>
                <h1 id="kanban-page-title">任务看板</h1>
                <p>选择一张任务卡，查看并操作它的执行模块。</p>
              </div>
              <div className="kanban-live-label"><span className="status-dot" /> 实时更新</div>
            </header>
            <KanbanBoard loading={loading} snapshot={snapshot} />
          </section>
        )}
      </div>
    </div>
  );
}
