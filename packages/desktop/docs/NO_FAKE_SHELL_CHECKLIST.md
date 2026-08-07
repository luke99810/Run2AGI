# No Fake Shell 检查表

每个功能完成后逐项核对：

- [ ] 每个可点击按钮都有真实 IPC/命令 handler，或明确禁用并说明原因。
- [ ] 不使用 React/localStorage 模拟暂停、停止、回退、成本或 Agent 状态。
- [ ] 不使用随机数、计时器、`Math.sin()` 伪造实时进度。
- [ ] Fixture 模式始终显式显示，不静默回退到 mid-flight。
- [ ] Fixture 模式永远不能发送 requirement 或执行控制命令。
- [ ] 引擎离线时需求输入仍可编辑；“正式派单”按钮保持禁用并显示真实缺口。
- [ ] 项目文件引用只返回已校验的相对路径、大小和哈希，不冒充已上传或已被 Agent 读取。
- [ ] 命令使用引擎握手提供的权威 `runId`，不使用状态源 ID 或路径哈希代替。
- [ ] 所有状态字段能指出来源文件、事件或命令回执。
- [ ] 命令只有在 `applied`/权威 snapshot 后才更新业务状态。
- [ ] 断线、超时、stale revision、拒绝都有可见反馈。
- [ ] “Agent 状态”不是 WorkPacket 冒充；缺 Agent contract 时使用“任务执行投影”。
- [ ] “甘特图”具有真实时间字段；缺时间 contract 时使用“依赖波次”。
- [ ] “实时”只有在 watch/subscribe 工作时使用。
- [ ] Packaging 使用 `process.resourcesPath`，不依赖用户安装 Python。
- [ ] 安装包在无 Node/Python/源码的干净环境验证。
- [ ] Release 附 SHA-256、版本说明、许可证清单和已知限制。
