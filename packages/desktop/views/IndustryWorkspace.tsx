import { useEffect, useState } from "react";
import type {
  AlgorithmProfile,
  IndustryKey,
  IndustryPhase,
  IndustryProfile,
} from "./industry-profiles";
import {
  INDUSTRY_PROFILES,
  PHASES,
  getIndustryProfile,
} from "./industry-profiles";

type AdaptMode = "auto" | "locked";
type AlgorithmKey = "scheduler" | "optimization" | "security" | "retrieval" | "decision";
type PlaybackSpeed = 1 | 2;

interface AppliedReceipt {
  readonly industry: IndustryKey;
  readonly mode: AdaptMode;
  readonly revision: number;
}

const SELECTABLE_INDUSTRIES = ["ops", "rd", "finance", "customer"] as const satisfies readonly IndustryKey[];

const ALGORITHM_CATEGORIES = [
  { id: "scheduler", name: "任务调度", marker: "01", subtitle: "安排执行顺序与资源" },
  { id: "optimization", name: "资源优化", marker: "02", subtitle: "缩短路径并平衡负载" },
  { id: "security", name: "安全保护", marker: "03", subtitle: "保护数据与通信过程" },
  { id: "retrieval", name: "智能检索", marker: "04", subtitle: "快速匹配知识与案例" },
  { id: "decision", name: "智能决策", marker: "05", subtitle: "根据规则选择下一步" },
] as const satisfies readonly {
  readonly id: AlgorithmKey;
  readonly name: string;
  readonly marker: string;
  readonly subtitle: string;
}[];

const MODE_LABELS: Readonly<Record<AdaptMode, string>> = {
  auto: "自动匹配",
  locked: "锁定方案",
};

const METRIC_BASE_VALUES: Readonly<Record<IndustryKey, readonly number[]>> = {
  ops: [18, 12.6, 64],
  rd: [8.4, 3.2, 86],
  finance: [4.8, 7.6, 0],
  customer: [62, 94, 1.8],
  general: [36, 98, 6.2],
};

const METRIC_VARIANCE: readonly number[] = [2.4, 0.8, 2.8];

function algorithmFor(profile: IndustryProfile, key: AlgorithmKey): AlgorithmProfile {
  return profile[key];
}

function metricValue(industry: IndustryKey, index: number, tick: number): string {
  const base = METRIC_BASE_VALUES[industry][index] ?? 0;
  const variance = METRIC_VARIANCE[index] ?? 1;
  const value = Math.max(0, base + Math.sin((tick + index * 2) * 0.72) * variance);
  return base < 15 ? value.toFixed(1) : Math.round(value).toString();
}

function metricLevel(index: number, tick: number): number {
  return Math.round(48 + index * 14 + Math.sin((tick + index) * 0.65) * 12);
}

function scrollToSection(id: string): void {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

export function IndustryWorkspace(): React.JSX.Element {
  const [industry, setIndustry] = useState<IndustryKey>("ops");
  const [mode, setMode] = useState<AdaptMode>("auto");
  const [selectedPhase, setSelectedPhase] = useState<IndustryPhase>(PHASES[0]);
  const [selectedAlgorithm, setSelectedAlgorithm] = useState<AlgorithmKey>("scheduler");
  const [selectedWorkerId, setSelectedWorkerId] = useState<string>(INDUSTRY_PROFILES.ops.workerRoles[0].id);
  const [receipt, setReceipt] = useState<AppliedReceipt | null>(null);
  const [isRunning, setIsRunning] = useState(true);
  const [speed, setSpeed] = useState<PlaybackSpeed>(1);
  const [liveTick, setLiveTick] = useState(0);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const profile = getIndustryProfile(industry);
  const phase = profile.phases.find((candidate) => candidate.id === selectedPhase)
    ?? profile.phases[0];
  const algorithm = algorithmFor(profile, selectedAlgorithm);
  const selectedWorker = profile.workerRoles.find((worker) => worker.id === selectedWorkerId)
    ?? profile.workerRoles[0];
  const livePhase = profile.phases[liveTick % profile.phases.length] ?? phase;
  const liveWorker = profile.workerRoles[liveTick % profile.workerRoles.length] ?? selectedWorker;
  const selectedPhaseIndex = profile.phases.findIndex((candidate) => candidate.id === selectedPhase);
  const livePhaseIndex = profile.phases.findIndex((candidate) => candidate.id === livePhase?.id);
  const appliedProfile = receipt === null ? null : getIndustryProfile(receipt.industry);
  const hasPendingChanges = receipt !== null
    && (receipt.industry !== industry || receipt.mode !== mode);

  useEffect(() => {
    if (!isRunning) {
      return undefined;
    }

    const timer = window.setInterval(() => {
      setLiveTick((current) => current + 1);
    }, speed === 2 ? 850 : 1700);

    return () => {
      window.clearInterval(timer);
    };
  }, [isRunning, speed]);

  if (phase === undefined) {
    throw new RangeError(`行业方案 ${profile.key} 缺少六阶段配置`);
  }

  const chooseIndustry = (key: IndustryKey): void => {
    const nextProfile = getIndustryProfile(key);
    setIndustry(key);
    setSelectedPhase(PHASES[0]);
    setSelectedAlgorithm("scheduler");
    setSelectedWorkerId(nextProfile.workerRoles[0]?.id ?? "");
    setLiveTick(0);
  };

  const applyProfile = (): void => {
    setReceipt((current) => ({
      industry,
      mode,
      revision: (current?.revision ?? 0) + 1,
    }));
  };

  const goToNextPhase = (): void => {
    const nextIndex = (selectedPhaseIndex + 1) % profile.phases.length;
    const nextPhase = profile.phases[nextIndex];
    if (nextPhase !== undefined) {
      setSelectedPhase(nextPhase.id);
    }
  };

  const activityItems = [0, 1, 2].map((offset) => {
    const phaseIndex = (liveTick - offset + profile.phases.length * 4) % profile.phases.length;
    const workerIndex = (liveTick - offset + profile.workerRoles.length * 4) % profile.workerRoles.length;
    return {
      id: `${liveTick}-${offset}`,
      phase: profile.phases[phaseIndex]?.name ?? "任务处理",
      worker: profile.workerRoles[workerIndex]?.name ?? "执行 Agent",
      time: offset === 0 ? "刚刚" : `${offset * 2} 秒前`,
    };
  });

  return (
    <main className="product-workspace" aria-labelledby="product-workspace-title">
      <nav className="product-category-bar" aria-label="行业方案分类">
        <div className="product-category-copy">
          <strong>行业方案</strong>
          <span>选择后立即预览完整执行组合</span>
        </div>
        <div className="product-category-list">
          {SELECTABLE_INDUSTRIES.map((key, index) => {
            const candidate = INDUSTRY_PROFILES[key];
            return (
              <button
                aria-pressed={industry === key}
                className={`product-category-item${industry === key ? " is-active" : ""}`}
                key={key}
                onClick={() => { chooseIndustry(key); }}
                type="button"
              >
                <span className="product-category-index">0{index + 1}</span>
                <strong>{candidate.name}</strong>
              </button>
            );
          })}
        </div>
      </nav>

      <section className="product-hero">
        <div className="product-hero-copy">
          <span className="product-eyebrow">AGENTTEAMS · 实时行业编排</span>
          <h1 className="product-title" id="product-workspace-title">
            <span>一套团队，</span>
            <span>动态适配</span>
            <em>{profile.name}</em>
          </h1>
          <p className="product-description">{profile.description} 六阶段流程、五类算法和三种 Worker 会随选择即时切换，并在右侧持续演示运行过程。</p>

          <div className="product-hero-actions">
            <button className="product-primary-action" onClick={() => { scrollToSection("product-flow"); }} type="button">
              查看执行流程
              <span aria-hidden="true">↓</span>
            </button>
            <button className="product-secondary-action" onClick={() => { scrollToSection("product-algorithms"); }} type="button">
              浏览算法组合
            </button>
          </div>

          <div className="product-config-row">
            <div className="product-mode-switch" role="group" aria-label="方案匹配方式">
              <button
                aria-pressed={mode === "auto"}
                className={`product-mode-button${mode === "auto" ? " is-active" : ""}`}
                onClick={() => { setMode("auto"); }}
                type="button"
              >
                自动匹配
              </button>
              <button
                aria-pressed={mode === "locked"}
                className={`product-mode-button${mode === "locked" ? " is-active" : ""}`}
                onClick={() => { setMode("locked"); }}
                type="button"
              >
                锁定方案
              </button>
            </div>
            <button className="product-apply-button" onClick={applyProfile} type="button">
              {receipt === null || hasPendingChanges ? "应用当前方案" : "重新应用"}
            </button>
          </div>

          {receipt !== null && (
            <div className={`product-receipt${hasPendingChanges ? " is-pending" : ""}`} role="status" aria-live="polite">
              <span>{hasPendingChanges ? "选择已更新，等待应用" : `第 ${receipt.revision} 次应用成功`}</span>
              <strong>{appliedProfile?.name} · {MODE_LABELS[receipt.mode]}</strong>
            </div>
          )}
        </div>

        <div className={`live-stage${isRunning ? "" : " is-paused"}`} aria-label="AgentTeams 实时运行演示">
          <div className="live-stage-toolbar">
            <div className="live-stage-title">
              <span className="live-status-dot" aria-hidden="true" />
              <div>
                <strong>{isRunning ? "团队正在执行" : "团队已暂停"}</strong>
                <small>{profile.name} · {MODE_LABELS[mode]}</small>
              </div>
            </div>
            <div className="live-stage-controls" role="group" aria-label="演示播放控制">
              <button className="live-control-button" onClick={() => { setIsRunning((current) => !current); }} type="button">
                {isRunning ? "暂停" : "继续"}
              </button>
              {([1, 2] as const).map((value) => (
                <button
                  aria-pressed={speed === value}
                  className={`live-control-button${speed === value ? " is-active" : ""}`}
                  key={value}
                  onClick={() => { setSpeed(value); }}
                  type="button"
                >
                  {value}x
                </button>
              ))}
            </div>
          </div>

          <div className="live-stage-canvas">
            <article className="live-node live-manager">
              <span className="live-node-label">01 · Manager</span>
              <strong>识别行业并加载方案</strong>
              <small>{profile.name}</small>
            </article>
            <span className="live-link" aria-hidden="true" />
            <article className="live-node live-leader">
              <span className="live-node-label">02 · TeamLeader</span>
              <strong>拆分任务并安排顺序</strong>
              <small>{profile.scheduler.algorithms.join(" + ")}</small>
            </article>
            <span className="live-link" aria-hidden="true" />
            <div className="live-worker-grid">
              {profile.workerRoles.map((worker) => (
                <article className={`live-node live-worker${liveWorker?.id === worker.id ? " is-active" : ""}`} key={worker.id}>
                  <span className="live-node-label">Worker</span>
                  <strong>{worker.name}</strong>
                </article>
              ))}
            </div>
          </div>

          <div className="live-summary">
            <div className="live-summary-item">
              <span>当前阶段</span>
              <strong>{livePhase?.name ?? phase.name}</strong>
            </div>
            <div className="live-summary-item">
              <span>活跃 Worker</span>
              <strong>{liveWorker?.name ?? "准备中"}</strong>
            </div>
            <div className="live-summary-item">
              <span>执行轮次</span>
              <strong>{String(liveTick + 1).padStart(2, "0")}</strong>
            </div>
          </div>

          <div className="live-activity">
            <div className="live-activity-head">
              <strong>实时动态</strong>
              <span>{isRunning ? "自动刷新中" : "刷新已暂停"}</span>
            </div>
            <ol className="live-activity-list" aria-live="polite">
              {activityItems.map((item, index) => (
                <li className={`live-activity-item${index === 0 ? " is-active" : ""}`} key={item.id}>
                  <span>{item.worker}</span>
                  <strong>正在处理「{item.phase}」</strong>
                  <small className="live-activity-time">{item.time}</small>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </section>

      <section className="product-section" id="product-flow" aria-labelledby="product-flow-title">
        <header className="product-section-heading">
          <div>
            <span className="product-section-kicker">01 · FLOW</span>
            <h2 className="product-section-title" id="product-flow-title">从需求到运营，一条流程连续推进</h2>
          </div>
          <p className="product-section-description">点击任意阶段查看任务内容，蓝色光点显示实时演示当前运行位置。</p>
        </header>

        <div className="product-stepper" role="tablist" aria-label="软件开发六阶段">
          {profile.phases.map((item, index) => (
            <button
              aria-controls="product-phase-panel"
              aria-selected={selectedPhase === item.id}
              className={`product-step${selectedPhase === item.id ? " is-active" : ""}${livePhaseIndex === index ? " is-live" : ""}${index < selectedPhaseIndex ? " is-complete" : ""}`}
              id={`product-phase-${item.id}`}
              key={item.id}
              onClick={() => { setSelectedPhase(item.id); }}
              role="tab"
              type="button"
            >
              <span className="product-step-number">{String(index + 1).padStart(2, "0")}</span>
              <strong className="product-step-label">{item.name}</strong>
            </button>
          ))}
        </div>

        <div
          aria-labelledby={`product-phase-${phase.id}`}
          className="product-phase-detail"
          id="product-phase-panel"
          role="tabpanel"
        >
          <div className="product-phase-meta">
            <span>阶段 {selectedPhaseIndex + 1} / {profile.phases.length}</span>
            <h3>{phase.name}</h3>
            <p>{profile.name}会在这个阶段依次完成以下动作。</p>
          </div>
          <ol className="product-phase-workflow">
            {phase.workflow.map((step, index) => (
              <li className="product-phase-workflow-item" key={step}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <strong>{step}</strong>
              </li>
            ))}
          </ol>
          <button className="product-phase-next" onClick={goToNextPhase} type="button">
            下一阶段
            <span aria-hidden="true">→</span>
          </button>
        </div>
      </section>

      <section className="product-section" id="product-algorithms" aria-labelledby="product-algorithm-title">
        <header className="product-section-heading">
          <div>
            <span className="product-section-kicker">02 · ENGINE</span>
            <h2 className="product-section-title" id="product-algorithm-title">五类算法，按行业即时装配</h2>
          </div>
          <p className="product-section-description">从左侧切换能力类别，右侧会立即展示当前行业采用的算法与运行策略。</p>
        </header>

        <div className="product-algorithm-layout">
          <div className="product-algorithm-menu" role="tablist" aria-label="算法类别">
            {ALGORITHM_CATEGORIES.map((category) => (
              <button
                aria-controls="product-algorithm-panel"
                aria-selected={selectedAlgorithm === category.id}
                className={`product-algorithm-button${selectedAlgorithm === category.id ? " is-active" : ""}`}
                id={`product-algorithm-${category.id}`}
                key={category.id}
                onClick={() => { setSelectedAlgorithm(category.id); }}
                role="tab"
                type="button"
              >
                <span className="product-algorithm-marker">{category.marker}</span>
                <span className="product-algorithm-button-copy">
                  <strong>{category.name}</strong>
                  <small>{category.subtitle}</small>
                </span>
              </button>
            ))}
          </div>

          <article
            aria-labelledby={`product-algorithm-${selectedAlgorithm}`}
            className="product-algorithm-panel"
            id="product-algorithm-panel"
            role="tabpanel"
          >
            <div className="product-algorithm-panel-head">
              <div>
                <span>{profile.name} · 当前组合</span>
                <h3>{ALGORITHM_CATEGORIES.find((item) => item.id === selectedAlgorithm)?.name}</h3>
              </div>
              <strong>{isRunning ? "运行中" : "等待继续"}</strong>
            </div>
            <div className="product-algorithm-badges">
              {algorithm.algorithms.map((name) => <span className="product-algorithm-badge" key={name}>{name}</span>)}
            </div>
            <div className="product-strategy">
              <span className="product-strategy-label">执行策略</span>
              <p>{algorithm.strategy}</p>
              <div className="product-strategy-meter" aria-hidden="true">
                <span className="product-strategy-fill" style={{ width: `${62 + (liveTick % 4) * 8}%` }} />
              </div>
            </div>
          </article>
        </div>
      </section>

      <section className="product-section" aria-labelledby="product-team-title">
        <header className="product-section-heading">
          <div>
            <span className="product-section-kicker">03 · TEAM</span>
            <h2 className="product-section-title" id="product-team-title">三层协作，Worker 随行业换岗</h2>
          </div>
          <p className="product-section-description">点击 Worker 查看职责，Manager 与 TeamLeader 负责保持整体协同。</p>
        </header>

        <div className="product-team-path" aria-label="AgentTeams 三层协作结构">
          <article className="product-team-tier">
            <span>Manager</span>
            <strong>识别行业 · 载入方案 · 协调资源</strong>
          </article>
          <span className="product-team-arrow" aria-hidden="true">→</span>
          <article className="product-team-tier">
            <span>TeamLeader</span>
            <strong>拆分任务 · 编排流程 · 分配 Worker</strong>
          </article>
        </div>

        <div className="product-worker-selector">
          {profile.workerRoles.map((worker, index) => (
            <button
              aria-pressed={selectedWorker?.id === worker.id}
              className={`product-worker-button${selectedWorker?.id === worker.id ? " is-active" : ""}`}
              key={worker.id}
              onClick={() => { setSelectedWorkerId(worker.id); }}
              type="button"
            >
              <span className="product-worker-avatar">W{index + 1}</span>
              <span>
                <small>Worker {String(index + 1).padStart(2, "0")}</small>
                <strong>{worker.name}</strong>
              </span>
            </button>
          ))}
        </div>

        {selectedWorker !== undefined && (
          <div className="product-worker-detail" aria-live="polite">
            <span className="product-worker-avatar">AG</span>
            <div className="product-worker-detail-copy">
              <small>当前选择</small>
              <h3>{selectedWorker.name}</h3>
              <p>{selectedWorker.description}</p>
            </div>
            <strong>{isRunning && liveWorker?.id === selectedWorker.id ? "正在执行" : "随时待命"}</strong>
          </div>
        )}
      </section>

      <section className="product-section" aria-labelledby="product-monitor-title">
        <header className="product-section-heading">
          <div>
            <span className="product-section-kicker">04 · LIVE DATA</span>
            <h2 className="product-section-title" id="product-monitor-title">运行数据持续更新</h2>
          </div>
          <p className="product-section-description">数据会跟随演示节奏轻微变化，暂停演示后数值同时停止刷新。</p>
        </header>

        <div className="product-monitor-grid" aria-live="polite">
          {profile.metrics.map((metric, index) => {
            const level = metricLevel(index, liveTick);
            return (
              <article className="product-metric" key={metric.id}>
                <div className="product-metric-head">
                  <span>{metric.name}</span>
                  <small>实时</small>
                </div>
                <div className="product-metric-value">
                  <strong>{metricValue(profile.key, index, liveTick)}</strong>
                  <span>{metric.unit}</span>
                </div>
                <div
                  aria-label={`${metric.name}趋势`}
                  aria-valuemax={100}
                  aria-valuemin={0}
                  aria-valuenow={level}
                  className="product-metric-track"
                  role="progressbar"
                >
                  <span className="product-metric-fill" style={{ width: `${level}%` }} />
                </div>
                <small className="product-metric-caption">{isRunning ? "刚刚完成一次更新" : "数值已暂停"}</small>
              </article>
            );
          })}
        </div>

        <div className="product-advanced">
          <button
            aria-expanded={showAdvanced}
            className="product-advanced-toggle"
            onClick={() => { setShowAdvanced((current) => !current); }}
            type="button"
          >
            <span>
              <strong>高级运行策略</strong>
              <small>查看资源不足或能力暂不可用时的处理方式</small>
            </span>
            <span aria-hidden="true">{showAdvanced ? "收起 −" : "展开 +"}</span>
          </button>
          {showAdvanced && (
            <div className="product-advanced-content">
              <article className="product-advanced-item">
                <span>能力切换</span>
                <strong>{profile.degradation.from} → {profile.degradation.to}</strong>
                <p>{profile.degradation.trigger}时，{profile.degradation.behavior}。</p>
              </article>
              <article className="product-advanced-item">
                <span>通用方案</span>
                <strong>自动保持任务运行</strong>
                <p>{profile.fallback.pluginMissing}；{profile.fallback.manualLock}。</p>
              </article>
            </div>
          )}
        </div>
      </section>
    </main>
  );
}
