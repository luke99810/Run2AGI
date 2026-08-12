# Bailian CNY Pricing Evidence

采集日期：2026-08-12

官方来源：<https://help.aliyun.com/zh/model-studio/model-pricing>

用途：为 Codentum 的 `Usage.cost_cny`、`WorkerOutcome.spent_cny` 和 Cost 视图提供可审计的人民币成本归因依据。

## 已落地价格

| Model ID | 采用口径 | 输入单价/百万 Token | 输出单价/百万 Token | 输入范围 |
|---|---|---:|---:|---|
| `qwen-coder-plus` | 官方行：无阶梯计价 | ¥3.5 | ¥7.0 | 无阶梯 |
| `qwen-coder-plus-1106` | 按 `qwen-coder-plus` 产品线归因 | ¥3.5 | ¥7.0 | 无阶梯 |
| `qwen-plus` | 官方行：当前能力等同 `qwen-plus-2025-12-01` | ¥0.8 | ¥2.0 | `0 < Token <= 128K` |
| `qwen-plus-latest` | 按 `qwen-plus` 当前别名归因 | ¥0.8 | ¥2.0 | `0 < Token <= 128K` |
| `qwen-plus-2025-11-05` | 按 `qwen-plus` 产品线 0-128K 档归因 | ¥0.8 | ¥2.0 | `0 < Token <= 128K` |
| `qwen-plus-2025-07-14` | 官方行：无阶梯计价 | ¥0.8 | ¥2.0 | 无阶梯 |
| `qwen3.6-plus` | 官方行：当前能力等同 `qwen3.6-plus-2026-04-02` | ¥2.0 | ¥12.0 | `0 < Token <= 256K` |
| `qwen3.6-plus-2026-04-02` | 官方固定版本行 | ¥2.0 | ¥12.0 | `0 < Token <= 256K` |

## 代码真源

价格表真源在 [bailian_pricing.py](/Users/cyan/Documents/比赛/codentum-main/packages/harness/codentum_harness/model_gateway/bailian_pricing.py)。

如果模型不在该表内，`ModelGateway` 仍按 `require_pricing=True` fail-closed，不把真实调用记成 ¥0。

如果模型命中阶梯价格但本次输入 Token 超出已审计档位，例如 `qwen-plus-2025-11-05` 超过 128K，`TokenPricing.cost_cny()` 会抛出 `ModelPricingRangeError`，避免用低档价格硬算高档请求。

## 不包含什么

- 不包含免费额度抵扣。账本记录毛成本，免费额度属于平台账单侧抵扣。
- 不包含 Batch 半价。Codentum 当前实时执行链路没有走 Batch。
- 不包含上下文缓存折扣。当前 `cached_input_tokens` 只有在 provider usage 明确返回且价格表给出缓存单价时才单独计算；本轮价格证据未把网页上的折扣口径写成固定缓存单价。
- 不包含未开通或未实测可调的 Kimi / GLM / DeepSeek 模型。
