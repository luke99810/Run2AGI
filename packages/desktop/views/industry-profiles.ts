export const PHASES = [
  "requirement",
  "design",
  "develop",
  "test",
  "release",
  "operate",
] as const;

export type IndustryPhase = (typeof PHASES)[number];
export type IndustryKey = "ops" | "rd" | "finance" | "customer" | "general";

export interface AlgorithmProfile {
  readonly algorithms: readonly string[];
  readonly strategy: string;
}

export interface WorkerRoleProfile {
  readonly id: string;
  readonly name: string;
  readonly description: string;
}

export interface PhaseProfile {
  readonly id: IndustryPhase;
  readonly name: string;
  readonly workflow: readonly string[];
}

export interface MonitorMetricProfile {
  readonly id: string;
  readonly name: string;
  readonly unit: string;
}

export interface IndustryFallbackProfile {
  readonly pluginMissing: string;
  readonly isolation: string;
  readonly manualLock: string;
}

export interface IndustryDegradationProfile {
  readonly trigger: string;
  readonly from: string;
  readonly to: string;
  readonly behavior: string;
}

export interface IndustryProfile {
  readonly key: IndustryKey;
  readonly name: string;
  readonly description: string;
  readonly pluginId: string;
  readonly scheduler: AlgorithmProfile;
  readonly optimization: AlgorithmProfile;
  readonly security: AlgorithmProfile;
  readonly retrieval: AlgorithmProfile;
  readonly decision: AlgorithmProfile;
  readonly workerRoles: readonly WorkerRoleProfile[];
  readonly phases: readonly PhaseProfile[];
  readonly metrics: readonly MonitorMetricProfile[];
  readonly fallback: IndustryFallbackProfile;
  readonly degradation: IndustryDegradationProfile;
}

const PHASE_NAMES: Readonly<Record<IndustryPhase, string>> = {
  requirement: "需求识别",
  design: "产品设计",
  develop: "开发迭代",
  test: "测试质保",
  release: "发布上线",
  operate: "持续运营",
};

function phase(id: IndustryPhase, ...workflow: readonly string[]): PhaseProfile {
  return { id, name: PHASE_NAMES[id], workflow };
}

const COMMON_FALLBACK: IndustryFallbackProfile = {
  pluginMissing: "加载通用流程、优先级加时间片轮转调度和 AES 基础加密",
  isolation: "单个行业插件加载失败时保持其他行业任务正常运行",
  manualLock: "允许手动锁定流程和算法组合，关闭自动切换",
};

const COMMON_DEGRADATION: IndustryDegradationProfile = {
  trigger: "高算力优化算法资源不足",
  from: "强化学习动态优化",
  to: "贪心优化",
  behavior: "保留当前任务上下文，切换轻量算法后继续执行",
};

export const INDUSTRY_PROFILES = {
  ops: {
    key: "ops",
    name: "运维监控",
    description: "面向告警归并、根因定位、自动修复和服务持续运营。",
    pluginId: "industry-plugin-ops",
    scheduler: {
      algorithms: ["抢占式优先级", "时间片轮转（RR）"],
      strategy: "P0 告警立即抢占，同级告警轮询分发，长巡检任务分片运行。",
    },
    optimization: {
      algorithms: ["贪心负载优化", "路径寻优"],
      strategy: "优先选择空闲节点，并合并重复巡检动作。",
    },
    security: {
      algorithms: ["AES", "RSA", "数据脱敏"],
      strategy: "加密账号和云密钥、保护 Agent 通信并脱敏敏感日志。",
    },
    retrieval: {
      algorithms: ["DBSCAN 告警聚类", "向量检索"],
      strategy: "归并同源告警，并检索历史故障处置方案。",
    },
    decision: {
      algorithms: ["RBAC 动态权限", "时序异常检测"],
      strategy: "高危操作触发多级审批，异常流量触发保护策略。",
    },
    workerRoles: [
      { id: "alert-cluster", name: "告警聚类 Agent", description: "合并重复告警并识别故障模式。" },
      { id: "root-cause", name: "根因定位 Agent", description: "结合拓扑和历史记录定位根因。" },
      { id: "auto-repair", name: "自动修复 Agent", description: "选择并执行经过校验的修复动作。" },
    ],
    phases: [
      phase("requirement", "聚类历史告警", "识别高频故障痛点"),
      phase("design", "设计告警分层调度", "规划监控数据加密存储"),
      phase("develop", "封装巡检与根因定位能力", "实现优先级和轮转调度"),
      phase("test", "执行并发压力测试", "验证安全加密链路"),
      phase("release", "灰度调度流量", "分批接入服务器集群"),
      phase("operate", "实时检测时序异常", "复盘并沉淀巡检能力"),
    ],
    metrics: [
      { id: "alert-backlog", name: "告警堆积量", unit: "条" },
      { id: "mttr", name: "平均恢复时长", unit: "分钟" },
      { id: "server-load", name: "服务器负载", unit: "%" },
    ],
    fallback: COMMON_FALLBACK,
    degradation: COMMON_DEGRADATION,
  },
  rd: {
    key: "rd",
    name: "研发自动化",
    description: "面向代码缺陷定位、自动修复、测试验证和持续交付。",
    pluginId: "industry-plugin-rd",
    scheduler: {
      algorithms: ["DAG-FCFS", "最短作业优先（SJF）"],
      strategy: "按定位、修复、测试的依赖顺序执行，优先调度短耗时并行测试。",
    },
    optimization: {
      algorithms: ["任务路径优化", "负载均衡"],
      strategy: "合并无依赖的并行测试任务，缩短迭代时间。",
    },
    security: {
      algorithms: ["SHA-256", "AES", "RSA"],
      strategy: "校验代码完整性、加密配置文件并安全分发开发者密钥。",
    },
    retrieval: {
      algorithms: ["代码向量检索", "缺陷意图分类"],
      strategy: "检索相似修复案例，并识别前端、后端和测试类缺陷。",
    },
    decision: {
      algorithms: ["RBAC 合并决策", "孤立森林异常识别"],
      strategy: "校验代码合并权限并识别恶意提交或漏洞代码。",
    },
    workerRoles: [
      { id: "defect-detect", name: "缺陷识别 Agent", description: "定位代码缺陷并完成类型分类。" },
      { id: "code-repair", name: "代码修复 Agent", description: "生成受约束的代码修改方案。" },
      { id: "automated-test", name: "自动化测试 Agent", description: "执行单元测试和回归验证。" },
    ],
    phases: [
      phase("requirement", "聚类缺陷记录", "统计高频 Bug 类型"),
      phase("design", "设计 DAG 流水线", "构建代码向量知识库"),
      phase("develop", "实现代码定位和自动修复", "封装单元测试能力"),
      phase("test", "验证端到端修复流程", "执行漏洞安全扫描"),
      phase("release", "执行蓝绿部署", "通过功能开关灰度发布"),
      phase("operate", "统计修复效率", "持续优化任务调度顺序"),
    ],
    metrics: [
      { id: "repair-duration", name: "代码修复时长", unit: "分钟" },
      { id: "bug-escape-rate", name: "Bug 逃逸率", unit: "%" },
      { id: "test-coverage", name: "测试覆盖率", unit: "%" },
    ],
    fallback: COMMON_FALLBACK,
    degradation: COMMON_DEGRADATION,
  },
  finance: {
    key: "finance",
    name: "金融风控",
    description: "面向风险核验、合规审计、理赔处置和监管时效。",
    pluginId: "industry-plugin-finance",
    scheduler: {
      algorithms: ["最早截止时间（EDF）", "抢占式优先级"],
      strategy: "按合规截止时间排序，超时风险单获得最高处理优先级。",
    },
    optimization: {
      algorithms: ["0/1 背包优化", "强化学习"],
      strategy: "在资源配额内选择高收益核验流程并动态调整核验深度。",
    },
    security: {
      algorithms: ["SM2", "SM3", "AES", "数据脱敏"],
      strategy: "使用国密签名和哈希、加密敏感数据并脱敏客户身份信息。",
    },
    retrieval: {
      algorithms: ["风险文本聚类", "Rerank 重排"],
      strategy: "检索相似判例并精准匹配合规条款。",
    },
    decision: {
      algorithms: ["多因子风险判定", "时序异常检测"],
      strategy: "多层审批拦截高风险交易并识别异常资金流。",
    },
    workerRoles: [
      { id: "risk-check", name: "风险核验 Agent", description: "综合风险信号完成风险定级。" },
      { id: "compliance-audit", name: "合规审计 Agent", description: "核对规则并生成合规检查结果。" },
      { id: "claim-handle", name: "理赔处置 Agent", description: "按风险与时效要求处理理赔任务。" },
    ],
    phases: [
      phase("requirement", "聚类风险信号", "识别高发欺诈场景"),
      phase("design", "设计 EDF 时效调度", "规划国密加密和多层风险决策"),
      phase("develop", "封装风险核验", "实现合规审计与理赔处置能力"),
      phase("test", "执行合规专项测试", "开展高并发时效压力测试"),
      phase("release", "极小比例灰度放量", "启用全链路交易日志审计"),
      phase("operate", "迭代风险决策", "留存判例知识库"),
    ],
    metrics: [
      { id: "risk-case-duration", name: "风险单处理时效", unit: "分钟" },
      { id: "compliance-block-rate", name: "合规拦截率", unit: "%" },
      { id: "encryption-errors", name: "加密报错次数", unit: "次" },
    ],
    fallback: COMMON_FALLBACK,
    degradation: COMMON_DEGRADATION,
  },
  customer: {
    key: "customer",
    name: "智能客服",
    description: "面向意图识别、智能问答、人工流转和客户满意度提升。",
    pluginId: "industry-plugin-customer",
    scheduler: {
      algorithms: ["会话哈希", "加权轮询"],
      strategy: "同一客户保持会话上下文，按负载权重分发海量咨询。",
    },
    optimization: {
      algorithms: ["成本贪心优化", "负载均衡"],
      strategy: "简单咨询调用轻量模型，复杂诉求调用高能力模型。",
    },
    security: {
      algorithms: ["AES", "PBKDF2", "数据脱敏"],
      strategy: "加密会话传输、脱敏隐私字段并匿名化对话日志。",
    },
    retrieval: {
      algorithms: ["TextCNN 意图分类", "向量检索"],
      strategy: "识别用户意图并检索历史相似对话解决方案。",
    },
    decision: {
      algorithms: ["敏感词风险判定", "RBAC 人工流转"],
      strategy: "自动拦截涉政和隐私对话，并转交人工坐席。",
    },
    workerRoles: [
      { id: "intent-detect", name: "意图识别 Agent", description: "识别咨询意图和服务等级。" },
      { id: "answer-execute", name: "问答执行 Agent", description: "检索知识并生成适配回答。" },
      { id: "satisfaction-check", name: "满意度核验 Agent", description: "评估服务结果并发起后续处理。" },
    ],
    phases: [
      phase("requirement", "聚类咨询意图", "划分咨询服务等级"),
      phase("design", "设计会话哈希调度", "规划对话隐私脱敏加密"),
      phase("develop", "实现意图识别和问答匹配", "封装满意度核验能力"),
      phase("test", "执行多渠道并发对话压测", "验证隐私安全规则"),
      phase("release", "按渠道灰度上线", "逐步启用客服能力"),
      phase("operate", "监控 NPS 与 CSAT", "持续优化意图分类"),
    ],
    metrics: [
      { id: "nps", name: "净推荐值（NPS）", unit: "分" },
      { id: "csat", name: "客户满意度（CSAT）", unit: "%" },
      { id: "first-response", name: "首次响应时长", unit: "秒" },
    ],
    fallback: COMMON_FALLBACK,
    degradation: COMMON_DEGRADATION,
  },
  general: {
    key: "general",
    name: "通用软件",
    description: "未识别软件类型时使用的通用六阶段开发流程。",
    pluginId: "industry-plugin-general",
    scheduler: { algorithms: ["优先级调度", "时间片轮转（RR）"], strategy: "按优先级处理并公平轮转同级任务。" },
    optimization: { algorithms: ["贪心优化"], strategy: "使用低资源开销的通用资源分配策略。" },
    security: { algorithms: ["AES", "SHA-256"], strategy: "提供基础数据加密和完整性校验。" },
    retrieval: { algorithms: ["关键词检索", "向量检索"], strategy: "组合精确匹配与语义检索。" },
    decision: { algorithms: ["RBAC 动态权限"], strategy: "按角色和任务上下文执行权限判断。" },
    workerRoles: [
      { id: "analyze", name: "需求分析 Agent", description: "澄清目标并拆分任务。" },
      { id: "implement", name: "实现 Agent", description: "完成方案实现和集成。" },
      { id: "verify", name: "验证 Agent", description: "执行测试并确认交付质量。" },
    ],
    phases: PHASES.map((id) => phase(id, PHASE_NAMES[id])),
    metrics: [
      { id: "throughput", name: "任务吞吐量", unit: "项/小时" },
      { id: "success-rate", name: "任务成功率", unit: "%" },
      { id: "latency", name: "平均处理时长", unit: "分钟" },
    ],
    fallback: COMMON_FALLBACK,
    degradation: COMMON_DEGRADATION,
  },
} as const satisfies Readonly<Record<IndustryKey, IndustryProfile>>;

export function getIndustryProfile(industry: string | null | undefined): IndustryProfile {
  switch (industry) {
    case "ops":
    case "rd":
    case "finance":
    case "customer":
    case "general":
      return INDUSTRY_PROFILES[industry];
    default:
      return INDUSTRY_PROFILES.general;
  }
}
