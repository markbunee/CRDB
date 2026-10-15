/**
 * 能力目录 → (1) 紧凑 systemPrompt  (2) typebox 参数 schema
 *
 * 为什么两边都从同一份 JSON 生成
 * ------------------------------
 * JSON 是 Python（校验/确认卡）与 Node（schema/prompt）的单一真源。
 * 若各写一份，必然出现「TS 侧允许的值 Python 侧拒绝」这类漂移，
 * 症状是模型输出的 plan 在后端被拒，而前端看起来一切正常 —— 极难排查。
 */
import { readFileSync } from "node:fs";
import { Type, type TSchema } from "typebox";
import { CAPABILITIES_PATH } from "./config.js";

export interface CapabilityJson {
  version?: number;
  slots: Record<string, SlotDef>;
  capabilities: Record<string, CapabilityDef>;
}

export interface SlotDef {
  label?: string;
  type?: string;
  group?: string;
  required?: boolean;
  default?: unknown;
  values?: string[];
  value_labels?: Record<string, string>;
  multi?: boolean;
  min?: number;
  max?: number;
  sub?: Record<string, SlotDef>;
}

export interface CapabilityDef {
  label?: string;
  desc?: string;
  backend?: string;
  aliases?: string[];
  fixed?: Record<string, unknown>;
  required?: string[];
  supports?: string[];
  db_keys?: string[];
}

let cached: CapabilityJson | null = null;

export function loadCatalog(): CapabilityJson {
  if (cached) return cached;
  cached = JSON.parse(readFileSync(CAPABILITIES_PATH, "utf-8")) as CapabilityJson;
  return cached;
}

/**
 * 生成 systemPrompt：刻意用紧凑 DSL 而不是 JSON Schema，
 * 同样信息量下 token 能省 5~10 倍，且前缀固定可命中 provider 的 prompt cache。
 */
export function buildSystemPrompt(cat: CapabilityJson, capabilityKeys: string[]): string {
  const lines: string[] = [];
  lines.push("你是医药销售数据库的「问数」助手。你的唯一任务是**补全查询参数**，");
  lines.push("不执行查询、不计算、不编造任何数字。");
  lines.push("");
  lines.push("可用能力（选且只选一个最匹配的）：");
  for (const k of capabilityKeys) {
    const c = cat.capabilities[k];
    if (!c) continue;
    const alias = (c.aliases || []).slice(0, 6).join("/");
    lines.push(`- ${k} ${c.label || ""}${alias ? ` | 触发词:${alias}` : ""}`);
  }
  lines.push("");
  lines.push("槽位（只填用户**明确提到**的，没提到的一律省略，不要替用户猜）：");
  lines.push("- region {level: 'nation'|'province'|'city', values: string[]}  统计范围");
  lines.push("- time {date_from: 'YYYY-MM-DD', date_to: 'YYYY-MM-DD'}  必须换算成绝对区间");
  lines.push("- product {names: string[]}  品类名，用用户原话里的词，不要编编码");
  lines.push("- metric 'boxes'|'amount'|'stores'|'both'  实销盒数/金额/门店数");
  lines.push("- store_type 'all'|'chain'|'franchise'  门店类型");
  lines.push("- compare 'none'|'yoy'|'mom'|'both'  同比/环比");
  lines.push("- split 'none'|'city'|'product'|'time'|'province'  是否拆表呈现");
  lines.push("- top_n number  排名前几");
  lines.push("");
  lines.push("硬性规则：");
  lines.push("1. 只输出一次 resolve_query 调用，参数就是上面的槽位。");
  lines.push("2. 用户没提到的槽位**必须省略**，由系统填默认值。");
  lines.push("3. 时间必须换成绝对日期；用户没说年份时，用数据最新日期所在年。");
  lines.push("4. 地域要填到 level：说省用 province，说市用 city，说全国/没说范围用 nation。");
  lines.push("5. 绝不编造品类编码、门店名、数字。");
  return lines.join("\n");
}

/** 把槽位的 values 转成 typebox 联合字面量类型；没有 values 时退回字符串。 */
function enumSchema(def: SlotDef | undefined): TSchema {
  const vals = def?.values;
  if (vals && vals.length) {
    return Type.Union(vals.map((v) => Type.Literal(v)));
  }
  return Type.String();
}

/**
 * 构造工具参数 schema。
 * 所有枚举取值**直接来自能力目录**，因此模型不可能产出后端不接受的值。
 */
export function buildParameters(cat: CapabilityJson, capabilityKeys: string[]): TSchema {
  const s = cat.slots || {};
  return Type.Object({
    capability: Type.Union(
      capabilityKeys.length ? capabilityKeys.map((k) => Type.Literal(k)) : [Type.Literal("q_overview")],
    ),
    region: Type.Optional(
      Type.Object({
        level: Type.Union([Type.Literal("nation"), Type.Literal("province"), Type.Literal("city")]),
        values: Type.Optional(Type.Array(Type.String())),
        expand_province: Type.Optional(Type.Boolean()),
      }),
    ),
    time: Type.Optional(
      Type.Object({
        date_from: Type.String({ pattern: "^\\d{4}-\\d{2}-\\d{2}$" }),
        date_to: Type.String({ pattern: "^\\d{4}-\\d{2}-\\d{2}$" }),
      }),
    ),
    product: Type.Optional(Type.Object({ names: Type.Array(Type.String()) })),
    metric: Type.Optional(enumSchema(s.metric)),
    store_type: Type.Optional(enumSchema(s.store_type)),
    compare: Type.Optional(enumSchema(s.compare)),
    split: Type.Optional(enumSchema(s.split)),
    top_n: Type.Optional(
      Type.Integer({ minimum: s.top_n?.min ?? 1, maximum: s.top_n?.max ?? 500 }),
    ),
  });
}
