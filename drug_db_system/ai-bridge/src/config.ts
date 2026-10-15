/**
 * ai-bridge 配置。
 *
 * **本服务不保存任何 LLM 密钥**：provider / model / api_key / base_url
 * 全部来自每次请求（由 Python 后端从该用户的配置里取出后透传）。
 * 这样多用户各用各的 Key，容器里不含密钥，也不存在并发串号问题。
 */

export const PORT = Number(process.env.PORT || 3000);

/** 能力目录 JSON 的路径（与 Python 侧共用同一份文件，挂载同一个卷） */
export const CAPABILITIES_PATH =
  process.env.CAPABILITIES_PATH || "/app/data/ai_capabilities.json";

/** 单次 LLM 调用超时（毫秒）：AI 只是补槽位，不该慢到拖垮提问 */
export const FILL_TIMEOUT_MS = Number(process.env.FILL_TIMEOUT_MS || 15000);

/**
 * 每次请求携带的模型配置（由 Python 后端透传，本服务不落盘、不写环境变量）。
 *
 * 定义放在 config.ts 而不是 agent.ts：agent 与 provider 互相需要这个类型，
 * 放在任一侧都会形成模块环（虽然 `import type` 编译后会被擦除，
 * 但环会让「谁依赖谁」变得难以阅读，也容易被后续改动破坏）。
 */
export interface LlmConfig {
  /** 借哪个内置 provider 抄「模型定义」字段（避免手写 Model 的必填项） */
  template: string;
  /** 请求协议：openai-completions / openai-responses / anthropic-messages */
  api: string;
  model: string;
  api_key: string;
  /** 后端已解析好的**实际**地址（用户没填时是供应商目录里的默认值） */
  base_url?: string;
}
