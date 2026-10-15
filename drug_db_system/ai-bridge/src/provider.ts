/**
 * 按用户配置构造 pi-ai 的 provider。
 *
 * 为什么不能直接拿内置 provider 用（三个坑，都是实测踩出来的）
 * -----------------------------------------------------------
 * 1. **协议不同**：OpenAI 官方走 Responses（/responses），Claude 走 Anthropic Messages，
 *    其余（智谱、月之暗面、通义、OneAPI、vLLM、Ollama…）都是 OpenAI Chat Completions。
 *    早先统一借 `openai` provider 实现，结果智谱的请求被发到 /responses 上 → 404。
 * 2. **地址不能靠猜**：内置 provider 的 baseUrl 是写死的，用户选了"智谱通用 API"
 *    却会打到 Coding 套餐端点。必须让请求里的 base_url 说了算。
 * 3. **模型名必须是用户填的那个**：`models.getModel(provider, id)` 只在
 *    **已注册的模型列表**里查找（models.ts 的 getModel 就是一次 find），
 *    用户填 `glm-4-flash` 这类不在内置清单里的名字会直接取不到模型。
 *
 * 解法：用 `createProvider` **自建**一个 provider ——
 * 协议按请求指定、地址用用户的、模型列表只放用户填的那一个。
 * 模型定义的必填字段（cost / input / reasoning 等）从同协议的内置 provider
 * 抄一个当模板，避免手写漏字段。
 */
import type { LlmConfig } from "./config.js";

/** 自建 provider 的 id（用固定值，避免与 pi 内置 provider 冲突） */
export const PROVIDER_ID = "crdb-llm";

/**
 * 静态密钥鉴权。
 *
 * 为什么不 setApiKeyAuth/进程环境变量：本服务并发处理不同用户的请求，
 * 把密钥写进 process.env 会串号（A 的话用了 B 的 Key）。
 * 这里把密钥闭包在 auth.resolve 里，每次请求各自独立。
 */
function staticKeyAuth(name: string, key: string): any {
  return {
    name,
    login: async () => ({ type: "api_key", key }),
    resolve: async () => ({ auth: { apiKey: key }, source: "request" }),
  };
}

/**
 * 按协议取 pi-ai 的流实现（动态 import：pi 未构建时不影响服务启动）。
 *
 * 与 agent.ts 同理，说明符用变量：pi 是可选的运行时依赖，
 * 没装时也让 tsc 能过（字面量会报 TS2307 中断镜像构建）。
 */
const PI_AI_ANTHROPIC: string = "@earendil-works/pi-ai/api/anthropic-messages.lazy";
const PI_AI_RESPONSES: string = "@earendil-works/pi-ai/api/openai-responses.lazy";
const PI_AI_COMPLETIONS: string = "@earendil-works/pi-ai/api/openai-completions.lazy";

async function apiImpl(kind: string): Promise<any> {
  if (kind === "anthropic-messages") {
    const m: any = await import(PI_AI_ANTHROPIC);
    return m.anthropicMessagesApi();
  }
  if (kind === "openai-responses") {
    const m: any = await import(PI_AI_RESPONSES);
    return m.openAIResponsesApi();
  }
  const m: any = await import(PI_AI_COMPLETIONS);
  return m.openAICompletionsApi();
}

export async function buildProvider(pi: any, llm: LlmConfig): Promise<any> {
  // ---- 1) 抄一份模型定义当模板 ----
  let proto: any = null;
  let tplBaseUrl = "";
  try {
    const mod: any = await import(`@earendil-works/pi-ai/providers/${llm.template}`);
    const factory = Object.values(mod).find((v) => typeof v === "function") as any;
    const tpl: any = factory ? factory() : null;
    tplBaseUrl = tpl?.baseUrl || "";
    proto = (tpl?.getAllModels?.() || [])[0] || null;
  } catch {
    // 模板拿不到（pi 的模型数据未生成等）→ 走下面的最小定义兜底
  }

  const baseUrl = (llm.base_url || "").trim() || tplBaseUrl;
  const model = proto
    ? { ...proto, id: llm.model, name: llm.model, provider: PROVIDER_ID, baseUrl }
    : {
        id: llm.model,
        name: llm.model,
        api: llm.api,
        provider: PROVIDER_ID,
        baseUrl,
        input: ["text"],
        cost: { input: 0, output: 0 },
        reasoning: false,
      };

  // ---- 2) 自建 provider：地址用用户的、模型只有用户那一个、协议按请求指定 ----
  return pi.createProvider({
    id: PROVIDER_ID,
    name: "CRDB LLM",
    baseUrl,
    auth: { apiKey: staticKeyAuth("CRDB LLM", llm.api_key) },
    models: [model],
    api: await apiImpl(llm.api),
  });
}
