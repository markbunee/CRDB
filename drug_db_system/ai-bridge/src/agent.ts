/**
 * pi agent 封装：**单步 slot-filling**。
 *
 * 为什么用「工具调用」而不是让模型直接吐 JSON 文本
 * -------------------------------------------------
 * 1. 结构化保证：模型必须调用 resolve_query 才能完成任务，参数是 typebox 校验过的，
 *    不会输出半截 JSON 或多余解释；
 * 2. 取值封闭：所有枚举来自能力目录，模型不可能产出后端不接受的值；
 * 3. 一步即停：`terminate: true` + `finishTurn: 'end'` 双保险，绝不多跑一轮。
 *
 * 模型配置由**每次请求**带上（来自用户在 CRDB 里保存的那一套），
 * 本服务不落任何密钥、不读环境变量里的密钥 —— 这样多用户各用各的 Key，
 * 容器被攻破也拿不到密钥。
 *
 * L2 的定位：只在规则路径（L0 提取 / L1 别名）连能力都没识别出来时才被调用，
 * 且它的输出**回到 Python 侧仍要过确认卡**（标为「AI 建议」），
 * 不会因为有了一个猜测值就直接执行 —— 这是「问全」的底线。
 */
import {
  buildParameters,
  buildSystemPrompt,
  loadCatalog,
  type CapabilityJson,
} from "./catalog.js";
import { FILL_TIMEOUT_MS, type LlmConfig } from "./config.js";
import { buildProvider, PROVIDER_ID } from "./provider.js";

// 对外仍从本模块导出 LlmConfig，server.ts 的 import 路径不用改
export type { LlmConfig } from "./config.js";

export interface FillInput {
  text: string;
  db_key: string;
  slots: Record<string, unknown>;
  capability: string | null;
  capabilities: string[];
  data_range: Record<string, unknown>;
  llm?: LlmConfig;
}

export interface FillResult {
  ok: boolean;
  capability?: string | null;
  slots: Record<string, unknown>;
  tokens: number;
  error?: string;
}

/**
 * pi 包是 file: 依赖且需先 build；未就绪时优雅返回 null，由 Python 侧走规则路径。
 *
 * 说明符**刻意用变量而不是字符串字面量**：pi 是可选的运行时依赖，
 * 没装（镜像里没带打包产物）时也要能通过 tsc 构建。
 * 字面量会让 tsc 报 TS2307（找不到模块）直接中断镜像构建；用变量则退化为 any ——
 * 本文件本来就全程以 any 使用 pi，没有实际损失。
 */
const PI_AGENT_CORE: string = "@earendil-works/pi-agent-core";
const PI_AI: string = "@earendil-works/pi-ai";

async function loadPi() {
  const agentCore: any = await import(PI_AGENT_CORE);
  const ai: any = await import(PI_AI);
  return {
    Agent: agentCore.Agent,
    createModels: ai.createModels,
    // createProvider 用于「按用户配置自建 provider」（见 provider.ts）
    createProvider: ai.createProvider,
  };
}

let piProbe: Promise<boolean> | null = null;

/**
 * pi 是否就绪（供 /health 自检）。
 *
 * 为 false 说明镜像里没有 pi 打包产物：/fill-slots 只会返回 503，
 * Python 侧静默退回规则路径 —— 用它一眼定位「配了模型却不生效」的原因。
 */
export function piAvailable(): Promise<boolean> {
  if (!piProbe) piProbe = loadPi().then(() => true).catch(() => false);
  return piProbe;
}

export async function fillSlots(input: FillInput): Promise<FillResult | null> {
  const llm = input.llm;
  if (!llm || !llm.api_key) {
    return null; // 用户没配模型 → 交给规则路径，不算错误
  }

  let cat: CapabilityJson;
  try {
    cat = loadCatalog();
  } catch (e) {
    return { ok: false, slots: {}, tokens: 0, error: `能力目录读取失败: ${(e as Error).message}` };
  }

  const keys = (input.capabilities?.length
    ? input.capabilities
    : Object.keys(cat.capabilities || {})) as string[];

  let pi: Awaited<ReturnType<typeof loadPi>>;
  try {
    pi = await loadPi();
  } catch (e) {
    return { ok: false, slots: {}, tokens: 0, error: `pi 未构建: ${(e as Error).message}` };
  }

  let models: any;
  try {
    models = pi.createModels();
    const provider = await buildProvider(pi, llm);
    models.setProvider(provider);
  } catch (e) {
    return { ok: false, slots: {}, tokens: 0, error: `provider 构造失败: ${(e as Error).message}` };
  }

  let model: any = null;
  try {
    // 查自建 provider（id = PROVIDER_ID），它的模型列表里只有用户填的那一个，
    // 所以不存在「模型名不在清单里」的问题
    model = models.getModel(PROVIDER_ID, llm.model);
  } catch (e) {
    model = null;
  }
  if (!model) {
    return {
      ok: false, slots: {}, tokens: 0,
      error: `模型不可用: ${llm.model}（协议 ${llm.api}，地址 ${llm.base_url || "未指定"}）`,
    };
  }

  const captured: { args?: Record<string, unknown> } = {};
  const tool: any = {
    name: "resolve_query",
    label: "补全查询参数",
    description: "把用户的中文问话补全为结构化查询参数。只调用一次。",
    parameters: buildParameters(cat, keys),
    execute: async (_id: string, params: any) => ({
      content: [{ type: "text", text: "已记录" }],
      details: params,
      terminate: true, // ★ 一步即停
    }),
  };

  const maxDate = String((input.data_range as any)?.max_date || "");
  const already = Object.entries(input.slots || {})
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
    .join("; ");
  const systemPrompt =
    buildSystemPrompt(cat, keys) +
    `\n\n当前数据库: ${input.db_key}\n数据最新日期: ${maxDate || "未知"}\n` +
    (already ? `规则已提取到的槽位（不要再改动，只需补缺失的）: ${already}\n` : "");

  const agent: any = new pi.Agent({
    initialState: {
      systemPrompt,
      model,
      tools: [tool],
      thinkingLevel: "off", // ★ 关掉 reasoning，省 token
    },
    streamFn: models.streamSimple.bind(models),
    // 密钥走回调注入，避免写 process.env 造成并发串号
    getApiKey: async () => llm.api_key,
    beforeToolCall: async (ctx: any) => {
      if (ctx?.toolCall?.name === "resolve_query") captured.args = ctx.args as any;
      return undefined;
    },
    afterToolCall: async () => ({ terminate: true }), // ★ 双保险
    finishTurn: async () => ({ action: "end" }), // ★ 兜底：绝不额外再跑一轮
  });

  const run = (async () => {
    await agent.prompt(input.text);
    return captured.args;
  })();

  const timeout = new Promise<null>((r) => setTimeout(() => r(null), FILL_TIMEOUT_MS));
  const args = await Promise.race([run, timeout]);
  if (!args) {
    return { ok: false, slots: {}, tokens: 0, error: "LLM 超时或未产出工具调用" };
  }

  const { capability, ...slots } = args as Record<string, unknown>;
  return {
    ok: true,
    capability: (capability as string) || input.capability,
    slots,
    tokens: estimateTokens(systemPrompt, input.text),
  };
}

/** 粗略估算（用于埋点观察趋势，不用于计费）：中文按字符数近似。 */
function estimateTokens(systemPrompt: string, text: string): number {
  return Math.ceil((systemPrompt.length + text.length) / 2.5) + 120;
}
