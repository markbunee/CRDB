/**
 * ai-bridge HTTP 服务（原生 node:http，零 Web 框架依赖）。
 *
 * 只有一个业务端点：POST /fill-slots —— 被 Python 的 routers/ai.py 调用。
 * 它**不执行查询**：只补槽位，查数永远在 Python 侧（复用既有统计函数与闸门）。
 * 它也不持有任何密钥：模型配置随请求进来，用完即弃。
 *
 * 服务不可用时（pi 未构建 / 请求没带模型配置）返回 503 而不是崩溃：
 * Python 侧对此有容错，会自动退回规则路径 —— 系统照常可用，只是长尾问法要人工补槽。
 */
import { createServer } from "node:http";
import { fillSlots, piAvailable, type LlmConfig } from "./agent.js";
import { PORT } from "./config.js";

function json(res: any, status: number, body: unknown) {
  const buf = Buffer.from(JSON.stringify(body), "utf-8");
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": buf.length,
  });
  res.end(buf);
}

async function readBody(req: any): Promise<any> {
  const chunks: Buffer[] = [];
  for await (const c of req) chunks.push(c as Buffer);
  if (!chunks.length) return {};
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf-8"));
  } catch {
    return {};
  }
}

function normalizeLlm(raw: any): LlmConfig | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const apiKey = String(raw.api_key || "").trim();
  const model = String(raw.model || "").trim();
  // template：借哪个内置 provider 抄模型定义；api：请求协议（两家必须都有）
  const template = String(raw.template || "").trim();
  const api = String(raw.api || "").trim() || "openai-completions";
  if (!apiKey || !model || !template) return undefined;
  return {
    template,
    api,
    model,
    api_key: apiKey,
    base_url: String(raw.base_url || "").trim(),
  };
}

const server = createServer(async (req, res) => {
  const url = (req.url || "").split("?")[0];

  if (req.method === "GET" && url === "/health") {
    // 不再上报「是否配了密钥」：密钥是请求级的，服务本身无状态。
    // pi_ready=false ⇒ 镜像里没带 pi 打包产物，/fill-slots 只会返回 503（L2 不生效）。
    return json(res, 200, {
      ok: true,
      mode: "per-request-llm",
      pi_ready: await piAvailable(),
    });
  }

  if (req.method === "POST" && url === "/fill-slots") {
    const body = await readBody(req);
    if (!body?.text) {
      return json(res, 400, { ok: false, error: "缺少 text" });
    }
    const llm = normalizeLlm(body.llm);
    if (!llm) {
      return json(res, 503, {
        ok: false,
        error: "请求未携带模型配置（provider/model/api_key），L2 兜底不可用",
      });
    }
    try {
      const out = await fillSlots({
        text: String(body.text),
        db_key: String(body.db_key || "dashenlin"),
        slots: body.slots || {},
        capability: body.capability ?? null,
        capabilities: Array.isArray(body.capabilities) ? body.capabilities : [],
        data_range: body.data_range || {},
        llm,
      });
      return json(res, out?.ok ? 200 : 503, out ?? { ok: false, error: "未产出结果" });
    } catch (e) {
      return json(res, 500, { ok: false, error: (e as Error).message });
    }
  }

  return json(res, 404, { ok: false, error: "not found" });
});

server.listen(PORT, () => {
  console.log(`[ai-bridge] listening on :${PORT}  (per-request llm config, no keys stored)`);
});
