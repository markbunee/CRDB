# -*- coding: utf-8 -*-
"""用户 LLM 模型配置：多套配置的增删改查 + 切换 + 连通性验证。

设计要点
--------
1. **按用户隔离**：每个人用自己的 API Key，互不可见（查询一律带 user_id 条件）。
2. **支持多套并切换**：借鉴 Skime 的 provider/model/api_key 字段，但补齐了它没有的
   「保存多套 + 一键切换」——业务上常见需求是「日常用便宜的 flash，复杂问题切强模型」。
3. **密钥掩码回显**：列表接口只回 `sk-****abcd`，绝不返回明文。
   编辑时若用户没重填密钥（传空），保留原值 —— 避免「只改模型名却把 key 清空」。
4. **明文存 SQLite**：与埋点同库（backend/data/ai_analytics.db），
   该目录已由 docker-compose 挂载持久化。之所以不加密：本服务与库同机同磁盘，
   加密密钥也只能放同一台机器，属于「防自己不防别人」的伪安全；
   真正的边界是「按用户隔离 + 不回显 + 接口需登录」。
5. **验证连通性**：所有列出的厂商都提供 OpenAI 兼容端点，
   因此统一用 `GET {base_url}/models` 带 Bearer 探测一次即可，
   不必为每家写一套 SDK 分支（Skime 那样按厂商分支，新增厂商就要改代码）。
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional

from ..config import DATA_DIR
from ..logger import get_logger

logger = get_logger("app.services.ai_model_config")

_DB = os.path.join(DATA_DIR, "ai_analytics.db")
_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_model_config (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    INTEGER NOT NULL,
  name       TEXT NOT NULL,
  provider   TEXT NOT NULL,
  model      TEXT NOT NULL,
  base_url   TEXT,
  api_key    TEXT NOT NULL,
  is_active  INTEGER NOT NULL DEFAULT 0,
  created_at TEXT,
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_model_cfg_user ON ai_model_config(user_id);
"""

_PROVIDERS_FILE = os.path.join(DATA_DIR, "ai_providers.json")
_prov_cache: Optional[dict] = None
_prov_mtime = -1.0


def _conn():
    import sqlite3
    c = sqlite3.connect(_DB, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with _LOCK:
            c = _conn()
            try:
                c.executescript(_SCHEMA)
                c.commit()
            finally:
                c.close()
    except Exception as e:
        logger.error("模型配置表初始化失败: %s", e)


# ---------- 供应商目录 ----------

def providers() -> Dict[str, dict]:
    """读 ai_providers.json（按 mtime 缓存），返回 {key: provider}。"""
    global _prov_cache, _prov_mtime
    try:
        mtime = os.path.getmtime(_PROVIDERS_FILE)
    except OSError:
        return {}
    if _prov_cache is not None and abs(mtime - _prov_mtime) < 1e-6:
        return _prov_cache
    try:
        with open(_PROVIDERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _prov_cache = {p["key"]: p for p in data.get("providers", [])}
        _prov_mtime = mtime
    except Exception as e:
        logger.error("供应商目录解析失败: %s", e)
    return _prov_cache or {}


def providers_list() -> List[dict]:
    """给前端的目录（不含任何密钥，字段与 ai_providers.json 一致）。"""
    import json as _json
    try:
        with open(_PROVIDERS_FILE, "r", encoding="utf-8") as f:
            return _json.load(f).get("providers", [])
    except Exception:
        return []


def _effective_base_url(provider: str, base_url: Optional[str]) -> str:
    """用户填了就用用户的（自建网关/代理常用），否则用目录里的默认地址。"""
    p = providers().get(provider) or {}
    return (base_url or "").strip() or (p.get("base_url") or "").strip()


def effective_base_url(provider: str, base_url: Optional[str] = None) -> str:
    """对外暴露：解析出**实际会用的** Base URL。

    为什么要暴露：用户在界面上不填 Base URL 时，保存下来的是空串，
    但插件/桥梁需要的是「空串→目录默认地址」解析之后的结果。
    早先直接把空串透传给 ai-bridge，那边只好用它自己硬编码的地址，
    结果选了「智谱通用 API」却打到了 Coding 套餐端点（或反之）。
    """
    return _effective_base_url(provider, base_url)


def api_kind(provider: str) -> str:
    """该供应商的请求协议。

    必须区分：OpenAI 官方走 Responses（/responses），Claude 走 Anthropic Messages，
    其余（含智谱、月之暗面、通义、自建网关）都是 OpenAI Chat Completions。
    早先统一借 openai provider 实现，导致智谱/自建网关被发到 /responses 上而 404。
    """
    p = providers().get(provider) or {}
    return p.get("api") or "openai-completions"


def model_template(provider: str) -> str:
    """模型定义模板来源（借哪个内置 provider 抄模型字段）。"""
    p = providers().get(provider) or {}
    return p.get("pi") or provider


# ---------- 掩码 ----------

def mask_key(key: str) -> str:
    """只回显尾部 4 位，前端据此展示「已配置」。"""
    k = (key or "").strip()
    if not k:
        return ""
    if len(k) <= 8:
        return "*" * len(k)
    return f"{k[:3]}****{k[-4:]}"


def _row_to_public(r) -> dict:
    return {
        "id": r["id"],
        "name": r["name"],
        "provider": r["provider"],
        "provider_label": (providers().get(r["provider"]) or {}).get("label", r["provider"]),
        "model": r["model"],
        "base_url": r["base_url"] or "",
        "api_key_masked": mask_key(r["api_key"]),
        "has_key": bool(r["api_key"]),
        "is_active": bool(r["is_active"]),
        "updated_at": r["updated_at"],
    }


# ---------- CRUD ----------

def list_configs(user_id: int) -> List[dict]:
    with _LOCK:
        c = _conn()
        try:
            rows = c.execute(
                "SELECT * FROM ai_model_config WHERE user_id=? ORDER BY is_active DESC, id DESC",
                (user_id,),
            ).fetchall()
            return [_row_to_public(r) for r in rows]
        finally:
            c.close()


def get_active(user_id: int) -> Optional[dict]:
    """取当前启用的配置（**含明文密钥**，仅供内部调用，不要直接回给前端）。"""
    with _LOCK:
        c = _conn()
        try:
            r = c.execute(
                "SELECT * FROM ai_model_config WHERE user_id=? AND is_active=1 LIMIT 1",
                (user_id,),
            ).fetchone()
            return dict(r) if r else None
        finally:
            c.close()


def save_config(
    user_id: int,
    name: str,
    provider: str,
    model: str,
    api_key: Optional[str],
    base_url: Optional[str] = None,
    config_id: Optional[int] = None,
) -> dict:
    """新建或更新一套配置。

    api_key 传空且是「更新」时保留原密钥（编辑弹窗不回显明文，用户通常不会重填）。
    第一套配置自动设为启用。
    """
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with _LOCK:
        c = _conn()
        try:
            if config_id:
                old = c.execute(
                    "SELECT * FROM ai_model_config WHERE id=? AND user_id=?",
                    (config_id, user_id),
                ).fetchone()
                if not old:
                    raise ValueError("配置不存在")
                key = (api_key or "").strip() or old["api_key"]
                c.execute(
                    "UPDATE ai_model_config SET name=?,provider=?,model=?,base_url=?,api_key=?,"
                    "updated_at=? WHERE id=? AND user_id=?",
                    (name, provider, model, base_url or "", key, now, config_id, user_id),
                )
                new_id = config_id
            else:
                key = (api_key or "").strip()
                if not key:
                    raise ValueError("首次保存必须填写 API Key")
                cur = c.execute(
                    "INSERT INTO ai_model_config(user_id,name,provider,model,base_url,api_key,"
                    "is_active,created_at,updated_at) VALUES(?,?,?,?,?,?,0,?,?)",
                    (user_id, name, provider, model, base_url or "", key, now, now),
                )
                new_id = int(cur.lastrowid)
                # 第一套自动启用
                cnt = c.execute(
                    "SELECT COUNT(*) n FROM ai_model_config WHERE user_id=?", (user_id,)
                ).fetchone()["n"]
                if cnt == 1:
                    c.execute(
                        "UPDATE ai_model_config SET is_active=1 WHERE id=?", (new_id,)
                    )
            c.commit()
            r = c.execute("SELECT * FROM ai_model_config WHERE id=?", (new_id,)).fetchone()
            return _row_to_public(r)
        finally:
            c.close()


def activate(user_id: int, config_id: int) -> bool:
    """切换启用项：先全部置 0，再把目标置 1（同一用户互斥）。"""
    with _LOCK:
        c = _conn()
        try:
            own = c.execute(
                "SELECT 1 FROM ai_model_config WHERE id=? AND user_id=?", (config_id, user_id)
            ).fetchone()
            if not own:
                return False
            c.execute("UPDATE ai_model_config SET is_active=0 WHERE user_id=?", (user_id,))
            c.execute(
                "UPDATE ai_model_config SET is_active=1, updated_at=? WHERE id=? AND user_id=?",
                (time.strftime("%Y-%m-%d %H:%M:%S"), config_id, user_id),
            )
            c.commit()
            return True
        finally:
            c.close()


def delete_config(user_id: int, config_id: int) -> bool:
    with _LOCK:
        c = _conn()
        try:
            r = c.execute(
                "SELECT is_active FROM ai_model_config WHERE id=? AND user_id=?",
                (config_id, user_id),
            ).fetchone()
            if not r:
                return False
            c.execute("DELETE FROM ai_model_config WHERE id=? AND user_id=?", (config_id, user_id))
            # 删掉的是启用项：顺延启用剩下最新的一套，避免用户「明明配了却用不了」
            if r["is_active"]:
                nxt = c.execute(
                    "SELECT id FROM ai_model_config WHERE user_id=? ORDER BY id DESC LIMIT 1",
                    (user_id,),
                ).fetchone()
                if nxt:
                    c.execute("UPDATE ai_model_config SET is_active=1 WHERE id=?", (nxt["id"],))
            c.commit()
            return True
        finally:
            c.close()


# ---------- 连通性验证 ----------

def _probe(url: str, api_key: str, timeout: int = 15):
    """探测一个 URL，返回 (http_status, body_text, transport_error)。

    HTTPError 也当「正常结果」返回而不是抛异常 —— 401/403 是有诊断价值的信息
    （说明路径对、Key 错），不该被当成网络故障。
    """
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {api_key.strip()}",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace"), None
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            body = ""
        return e.code, body, None
    except Exception as e:
        return 0, "", f"{type(e).__name__}: {e}"


def validate(provider: str, model: str, api_key: str, base_url: Optional[str] = None) -> dict:
    """测试连接（统一 GET /models 探测，不按厂商写 SDK 分支）。

    为什么统一用 /models 而不是按厂商分支：目录里所有厂商都提供 OpenAI 兼容端点，
    统一探测意味着**新增厂商无需改代码**；Skime 那种按厂商写 SDK 分支的做法，
    每加一家都要动后端。

    **不猜路径，而是逐个试** —— Base URL 的写法各家不一：
      - DeepSeek  `https://api.deepseek.com`              需要补 /v1
      - 月之暗面  `https://api.moonshot.cn/v1`            自带 /v1
      - 智谱      `https://open.bigmodel.cn/api/paas/v4`  既不带 /v1，补了反而错
    早先"不以 /v1 结尾就补 /v1"的写法会把智谱拼成
    `.../paas/v4/v1/models`（不存在）→ 用户永远测不通，还会以为是自己的 Key 有问题。
    这类"路径拼错伪装成密钥错误"最难排查，所以改成候选依次探测：
    401/403/429 立即返回（说明路径对、问题在密钥或额度），只有 404 才继续试下一个。
    """
    p = providers().get(provider)
    if not p:
        return {"valid": False, "message": f"未知供应商: {provider}"}

    base = _effective_base_url(provider, base_url)
    if not base:
        return {"valid": False, "message": "缺少 Base URL（自定义供应商必须填写）"}
    if not (api_key or "").strip():
        return {"valid": False, "message": "缺少 API Key"}

    b = base.rstrip("/")
    candidates = [f"{b}/models"]
    if not b.endswith("/v1"):
        candidates.append(f"{b}/v1/models")

    status, body, transport_err = 0, "", None
    used_url = candidates[0]
    hit = False
    for url in candidates:
        status, body, transport_err = _probe(url, api_key)
        used_url = url
        if transport_err:
            continue
        if status in (200, 401, 403, 429):
            hit = True
            break
    if not hit:
        last = transport_err or f"HTTP {status} {body}".strip()
        return {
            "valid": False,
            "message": f"地址不通或路径不对（已尝试 {len(candidates)} 个地址）。最后错误：{last}",
            "probe_url": used_url,
        }

    if status == 200:
        # 解析可选：能列出模型最好，列不出也算通过（部分网关不实现 /models）
        models: List[str] = []
        try:
            data = json.loads(body)
            models = [str(m.get("id")) for m in (data.get("data") or []) if m.get("id")]
        except Exception:
            pass
        msg = "连接成功"
        if models and model and model not in models:
            msg = f"连接成功，但模型「{model}」不在返回列表里（仍可使用，请确认模型名拼写）"
        return {"valid": True, "message": msg, "models": models[:200],
                "base_url": base, "probe_url": used_url}

    hint = {
        401: "密钥无效或已过期",
        403: "密钥无权限",
        429: "请求过于频繁或余额不足",
    }.get(status, "")
    return {"valid": False, "message": f"HTTP {status} {hint} {body}".strip(),
            "http_status": status, "probe_url": used_url}
