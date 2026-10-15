# -*- coding: utf-8 -*-
"""AI 问数接口：/api/{db_key}/ai/*（对话）+ /api/ai-admin/*（埋点后台）

主流程（默认路径 0 token）
-------------------------
    ask  → 确定性提取 → 三态判定 → 必填缺失？
              ├─ 否 → 直接执行 → 回答 + 口径说明
              └─ 是 → 生成确认卡（问全）→ 用户点选 → confirm → 执行

L2（LLM）只在「连能力都没识别出来」时才调 ai-bridge，
且它的输出**仍然要过确认卡**（标为 AI 建议，用户点一下才生效）——
绝不因为模型猜了一个值就盲目执行，这正是「问全」的意义。

为什么用 urllib 而不是 httpx/requests
-------------------------------------
后端 requirements 里没有 HTTP 客户端依赖，加一个就要重建镜像；
这里只需要一次简单的 JSON POST，标准库足够，保持零新增依赖。
"""
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..dependencies import get_current_user, require_admin, validate_db_key
from ..logger import get_logger
from ..services import ai_analytics as ana
from ..services import ai_capabilities as caps
from ..services import ai_clarify, ai_executor, ai_slots
from ..services import ai_model_config as mc
from ..services.query_gate import QueryRejected, acquire as query_acquire

logger = get_logger("app.routers.ai")

router = APIRouter(prefix="/api/{db_key}", tags=["ai"])
admin_router = APIRouter(prefix="/api/ai-admin", tags=["ai-admin"])
# 模型配置：登录用户各自维护自己的，不需要管理员权限
models_router = APIRouter(prefix="/api/ai-models", tags=["ai-models"])

# ai-bridge（Node / pi）地址；留空或关闭时，L2 兜底不可用，但 L0/L1 全覆盖路径照常工作
AI_BRIDGE_URL = os.environ.get("AI_BRIDGE_URL", "http://ai:3000").rstrip("/")
AI_BRIDGE_ENABLED = os.environ.get("AI_BRIDGE_ENABLED", "false").lower() == "true"
AI_BRIDGE_TIMEOUT = float(os.environ.get("AI_BRIDGE_TIMEOUT", "8"))
# 置信度低于此值才考虑让 LLM 参与预填（高置信的规则结果不需要花钱再问一遍模型）
LLM_FILL_THRESHOLD = float(os.environ.get("AI_LLM_FILL_THRESHOLD", "0.6"))


class AskIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=300)
    session_id: Optional[str] = None


class ConfirmIn(BaseModel):
    session_id: str
    question_id: Optional[int] = None
    answers: Dict[str, Any] = Field(default_factory=dict)
    edited_from: Optional[str] = None


class FeedbackIn(BaseModel):
    question_id: int
    rating: int = Field(..., ge=-1, le=1)
    comment: Optional[str] = None


def _bridge_fill(text: str, db_key: str, rs, user: dict) -> Optional[Dict[str, Any]]:
    """L2：让 ai-bridge（pi agent，单步 tool call）补齐槽位。

    只在「能力都没识别出来」或置信度过低时调用；返回的建议仍标为待确认。
    任何失败都不影响主流程 —— 规则路径本身就是可用的。

    模型配置是**每个用户自己的**：没配就静默跳过 L2（不报错），
    key 也不落到 ai-bridge 上，每次请求透传，容器里不存任何密钥。
    """
    if not AI_BRIDGE_ENABLED:
        return None
    cfg = mc.get_active(user.get("id"))
    if not cfg:
        return None  # 该用户还没配模型 → 走规则路径
    payload = {
        "text": text,
        "db_key": db_key,
        "slots": {k: v.get("value") for k, v in rs.slots.items()},
        "capability": rs.capability,
        "capabilities": list(caps.available_capabilities(db_key).keys()),
        "data_range": rs.data_range,
        "llm": {
            # template: 借哪个内置 provider 抄「模型定义」（避免手写 Model 的必填字段）
            "template": mc.model_template(cfg["provider"]),
            # api: 请求协议，决定桥梁用哪个 pi-ai 实现（三家不一样，不能统一）
            "api": mc.api_kind(cfg["provider"]),
            "model": cfg["model"],
            "api_key": cfg["api_key"],
            # 传**解析后**的实际地址：用户没填时这里是目录默认值，
            # 否则桥梁只能用它硬编码的地址，会出现「选了通用 API 却打 Coding 端点」
            "base_url": mc.effective_base_url(cfg["provider"], cfg.get("base_url")),
        },
    }
    try:
        req = urllib.request.Request(
            f"{AI_BRIDGE_URL}/fill-slots",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=AI_BRIDGE_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, dict) else None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        logger.warning("ai-bridge 不可用（走规则路径）: %s", e)
        return None


def _run_guarded(plan: dict, db_key: str, user: dict) -> dict:
    """带并发闸门的执行。

    为什么要单独加闸门：main.py 的 query_gate 中间件只拦 **GET**，
    而 AI 的执行入口是 POST（/ai/confirm）。不在这里补一道，
    AI 就成了绕开限流的合法后门 —— 用户连问几句就能把小内存机器打满。
    """
    freq_key = json.dumps(plan, sort_keys=True, ensure_ascii=False, default=str)
    try:
        slot = query_acquire(user.get("id"), path="/ai/execute", freq_key=freq_key)
    except QueryRejected as e:
        raise HTTPException(429, e.message, headers={"Retry-After": str(e.retry_after)})
    try:
        return ai_executor.execute(plan, db_key)
    finally:
        slot.release()


@router.post("/ai/ask")
def ask(db_key: str = validate_db_key, body: AskIn = ..., user: dict = Depends(get_current_user)):
    """提问 → 返回「确认卡」或「直接回答」。

    返回体二选一：
      {"mode":"card",   "card":{...}}   必填槽有缺失/歧义，需要人补（问全）
      {"mode":"answer", ...}            槽位齐了，已执行
    """
    t0 = time.time()
    text = body.text.strip()
    sid, uid = body.session_id or ana.new_session(db_key, user.get("id")), user.get("id")

    rs = ai_slots.extract(text, db_key)

    # ---- L2 兜底：只在能力没识别 / 置信过低时让模型帮着预填 ----
    route = "L0_rule" if rs.capability else "L1_template"
    llm_tokens = 0
    if rs.capability is None or rs.confidence < LLM_FILL_THRESHOLD:
        filled = _bridge_fill(text, db_key, rs, user)
        if filled:
            route = "L2_llm"
            llm_tokens = int(filled.get("tokens") or 0)
            if filled.get("capability") and not rs.capability:
                rs.capability = filled["capability"]
            for k, v in (filled.get("slots") or {}).items():
                if v in (None, "", []):
                    continue
                cur = rs.slots.get(k) or {}
                if cur.get("state") in ("missing", "ambiguous"):
                    # 标记为待确认（黄）：模型只是建议，仍要点一下才执行
                    rs.slots[k] = {"state": "ambiguous", "value": v, "note": "AI 建议，请确认"}

    card = ai_clarify.build_card(rs, sid)
    qid = ana.record_question(
        session_id=sid, user_id=uid, db_key=db_key, raw_text=text,
        slots={k: v.get("value") for k, v in rs.slots.items()},
        confidence=rs.confidence, unresolved=rs.unresolved,
        route=route, capability=rs.capability, llm_tokens=llm_tokens,
        status="clarified" if not card["ready"] else "answered",
    )
    card["question_id"] = qid
    for m in card["missing"]:
        ana.record_clarification(qid, m, f"缺少必填槽位: {m}")

    if not card["ready"]:
        ana.save_session(sid, {"card": card, "slots": {k: v.get("value") for k, v in rs.slots.items()},
                               "capability": rs.capability, "question_id": qid})
        return {"mode": "card", "card": card}

    plan = ai_clarify.to_plan(card, {k: v.get("value") for k, v in rs.slots.items()
                                     if v.get("value") not in (None, "", [])})
    plan["capability"] = rs.capability
    errs = caps.validate_plan(rs.capability, plan, db_key)
    if errs:
        card["missing"] = errs
        card["ready"] = False
        return {"mode": "card", "card": card}

    res = _run_guarded(plan, db_key, user)
    ana.update_question(qid, status="answered" if res.get("ok") else "failed",
                        answer_ms=int((time.time() - t0) * 1000))
    ana.record_execution(qid, plan, sql_ms=res.get("cost_ms"), row_count=1)
    return {"mode": "answer", "question_id": qid, "session_id": sid, **res}


@router.post("/ai/confirm")
def confirm(db_key: str = validate_db_key, body: ConfirmIn = ...,
            user: dict = Depends(get_current_user)):
    """确认卡提交 → 合并答案 → 校验 → 执行。

    edited_from：前端传用户**手动改动过**的字段名，埋点据此定位提取器错在哪。
    """
    t0 = time.time()
    sess = ana.load_session(body.session_id)
    if not sess:
        raise HTTPException(400, "会话不存在或已过期，请重新提问")

    try:
        state = json.loads(sess["state_json"] or "{}")
    except Exception:
        state = {}
    card = state.get("card") or {}
    card["db_key"] = db_key

    merged = dict(state.get("slots") or {})
    for k, v in (body.answers or {}).items():
        if v not in (None, "", []):
            merged[k] = v

    plan = ai_clarify.to_plan(card, merged)
    cap_key = plan.get("capability") or card.get("capability")
    plan["capability"] = cap_key

    plan = caps.apply_defaults(cap_key, plan)
    errs = caps.validate_plan(cap_key, plan, db_key)
    if errs:
        return {"mode": "card", "card": {**card, "missing": errs, "ready": False}}

    res = _run_guarded(plan, db_key, user)
    qid = body.question_id or state.get("question_id")
    if qid:
        ana.update_question(qid, status="answered" if res.get("ok") else "failed",
                            answer_ms=int((time.time() - t0) * 1000))
        ana.record_execution(qid, plan, edited=bool(body.edited_from),
                             edited_from=body.edited_from or "", sql_ms=res.get("cost_ms"))
        for k, v in (body.answers or {}).items():
            if v not in (None, "", []):
                ana.record_clarification(qid, k, "确认卡提交", user_choice=v,
                                         choice_type="click_option")
    return {"mode": "answer", "question_id": qid, "session_id": body.session_id, **res}


@router.post("/ai/abandon")
def abandon(db_key: str = validate_db_key, body: ConfirmIn = ...,
            user: dict = Depends(get_current_user)):
    """用户放弃（反问太多/问错了）—— 这是最重要的负面信号，必须记下来。"""
    if body.question_id:
        ana.update_question(body.question_id, status="abandoned")
    return {"ok": True}


@router.post("/ai/feedback")
def feedback(db_key: str = validate_db_key, body: FeedbackIn = ...,
             user: dict = Depends(get_current_user)):
    ana.record_feedback(body.question_id, body.rating, body.comment or "")
    return {"ok": True}


# ---------------- 模型配置（每个登录用户各自维护） ----------------

class ModelConfigIn(BaseModel):
    id: Optional[int] = None
    name: str = Field(..., min_length=1, max_length=40, description="配置别名，如「DeepSeek 主力」")
    provider: str = Field(..., min_length=1, max_length=40)
    model: str = Field(..., min_length=1, max_length=80)
    api_key: Optional[str] = Field(None, max_length=300, description="更新时留空表示保留原密钥")
    base_url: Optional[str] = Field(None, max_length=300, description="留空用供应商默认地址")


class ModelValidateIn(BaseModel):
    provider: str
    model: str = ""
    api_key: str
    base_url: Optional[str] = None


@models_router.get("/providers")
def list_providers(_user: dict = Depends(get_current_user)):
    """可选供应商目录（不含任何密钥）——前端下拉直接渲染它，加厂商不用改前端。"""
    return {"providers": mc.providers_list()}


@models_router.get("")
def list_my_configs(user: dict = Depends(get_current_user)):
    """我保存的模型配置（密钥已掩码，只回显尾部 4 位）。"""
    return {"items": mc.list_configs(user["id"])}


@models_router.post("")
def save_my_config(body: ModelConfigIn, user: dict = Depends(get_current_user)):
    """新建或更新一套配置。"""
    try:
        item = mc.save_config(
            user_id=user["id"], name=body.name.strip(), provider=body.provider.strip(),
            model=body.model.strip(), api_key=body.api_key,
            base_url=body.base_url, config_id=body.id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "item": item}


@models_router.post("/{config_id}/activate")
def activate_config(config_id: int, user: dict = Depends(get_current_user)):
    """切换当前启用的配置（业务上「日常用便宜的，复杂问题切强模型」）。"""
    if not mc.activate(user["id"], config_id):
        raise HTTPException(404, "配置不存在")
    return {"ok": True}


@models_router.delete("/{config_id}")
def delete_config(config_id: int, user: dict = Depends(get_current_user)):
    if not mc.delete_config(user["id"], config_id):
        raise HTTPException(404, "配置不存在")
    return {"ok": True}


@models_router.post("/validate")
def validate_model(body: ModelValidateIn, user: dict = Depends(get_current_user)):
    """测试连通性 —— 保存前就能先测，避免存一堆用不了的配置。"""
    return mc.validate(
        provider=body.provider.strip(), model=(body.model or "").strip(),
        api_key=body.api_key, base_url=body.base_url,
    )


# ---------------- 埋点后台（仅管理员） ----------------

@admin_router.get("/overview")
def admin_overview(_admin: dict = Depends(require_admin)):
    """0 token 覆盖率 / 反问率 / 放弃率 —— 验证优化是否真的生效。"""
    return ana.overview()


@admin_router.get("/questions")
def admin_questions(limit: int = 50, status: str = "",
                    _admin: dict = Depends(require_admin)):
    return {"items": ana.recent_questions(limit=limit, status=status)}


@admin_router.get("/unresolved")
def admin_unresolved(limit: int = 30, _admin: dict = Depends(require_admin)):
    """没解析出的高频词 → 直接拿去补 aliases / 模板库。"""
    return {"items": ana.unresolved_top(limit=limit)}


@admin_router.get("/slot-choices")
def admin_slot_choices(slot: str = "", limit: int = 30,
                       _admin: dict = Depends(require_admin)):
    """槽位选择分布 → 高频项升级为默认建议值，反问次数自然下降。"""
    return {"items": ana.slot_choice_stats(slot=slot, limit=limit)}
