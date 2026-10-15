# -*- coding: utf-8 -*-
"""AI 埋点：SQLite 落库（问题 / 澄清 / 执行 / 反馈）+ 会话态存储。

为什么用 SQLite 而不是塞进业务库
--------------------------------
埋点是**观测数据**，与三张业务库（dashenlin/gaoji/haiwang）生命周期无关：
业务库会被导入/清空，埋点必须独立留存，否则一清空历史问法全没了，优化闭环断掉。
落在 backend/data/ 下与 product_map.json 同级，已被 docker-compose 挂载持久化。

这张表要回答的问题（决定字段设计）
----------------------------------
1. 有多少问法走了 0 token 路径？            → route + llm_tokens
2. 哪些词没解析出来（要补别名/模板）？      → unresolved
3. 每个槽位用户实际选了什么（要生成默认值）？→ ai_clarification.user_choice
4. 用户手动改了哪个字段（= 提取器错哪了）？ → ai_execution.edited_from
5. 反问后用户跑了（问太多/问错了）？        → status=abandoned + clarify_count
"""
import json
import os
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from ..config import DATA_DIR
from ..logger import get_logger

logger = get_logger("app.services.ai_analytics")

_DB = os.path.join(DATA_DIR, "ai_analytics.db")
_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_question (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id     TEXT,
  user_id        INTEGER,
  db_key         TEXT,
  raw_text       TEXT,
  created_at     TEXT,
  slots_json     TEXT,
  confidence     REAL,
  unresolved     TEXT,
  route          TEXT,
  capability     TEXT,
  llm_tokens     INTEGER DEFAULT 0,
  status         TEXT,
  clarify_count  INTEGER DEFAULT 0,
  answer_ms      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_q_created ON ai_question(created_at);
CREATE INDEX IF NOT EXISTS idx_q_status  ON ai_question(status);

CREATE TABLE IF NOT EXISTS ai_clarification (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  question_id  INTEGER,
  slot         TEXT,
  asked_text   TEXT,
  options_json TEXT,
  user_choice  TEXT,
  choice_type  TEXT,
  created_at   TEXT
);

CREATE TABLE IF NOT EXISTS ai_execution (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  question_id  INTEGER,
  plan_json    TEXT,
  plan_edited  INTEGER DEFAULT 0,
  edited_from  TEXT,
  cache_hit    INTEGER DEFAULT 0,
  sql_ms       INTEGER,
  row_count    INTEGER,
  created_at   TEXT
);

CREATE TABLE IF NOT EXISTS ai_feedback (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  question_id INTEGER,
  rating      INTEGER,
  comment     TEXT,
  created_at  TEXT
);

CREATE TABLE IF NOT EXISTS ai_session (
  session_id  TEXT PRIMARY KEY,
  db_key      TEXT,
  user_id     INTEGER,
  state_json  TEXT,
  updated_at  TEXT
);
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    """建表（幂等）。在 app startup 里调用一次。"""
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
        logger.error("AI 埋点库初始化失败: %s", e)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ---------- 写入 ----------

def new_session(db_key: str, user_id: Optional[int]) -> str:
    sid = uuid.uuid4().hex[:16]
    try:
        with _LOCK:
            c = _conn()
            try:
                c.execute(
                    "INSERT OR REPLACE INTO ai_session(session_id,db_key,user_id,state_json,updated_at)"
                    " VALUES(?,?,?,?,?)", (sid, db_key, user_id, "{}", _now()))
                c.commit()
            finally:
                c.close()
    except Exception as e:
        logger.error("创建会话失败: %s", e)
    return sid


def save_session(sid: str, state: dict) -> None:
    try:
        with _LOCK:
            c = _conn()
            try:
                c.execute("UPDATE ai_session SET state_json=?, updated_at=? WHERE session_id=?",
                          (json.dumps(state, ensure_ascii=False), _now(), sid))
                c.commit()
            finally:
                c.close()
    except Exception as e:
        logger.error("保存会话失败: %s", e)


def load_session(sid: str) -> Optional[dict]:
    try:
        with _LOCK:
            c = _conn()
            try:
                r = c.execute("SELECT * FROM ai_session WHERE session_id=?", (sid,)).fetchone()
                return dict(r) if r else None
            finally:
                c.close()
    except Exception as e:
        logger.error("读取会话失败: %s", e)
        return None


def record_question(**kw) -> int:
    """记录一条提问，返回 question_id（后续埋点用）。"""
    sql_ = ("INSERT INTO ai_question(session_id,user_id,db_key,raw_text,created_at,"
            "slots_json,confidence,unresolved,route,capability,llm_tokens,status,"
            "clarify_count,answer_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
    vals = (
        kw.get("session_id"), kw.get("user_id"), kw.get("db_key"), kw.get("raw_text"), _now(),
        json.dumps(kw.get("slots"), ensure_ascii=False) if kw.get("slots") else None,
        kw.get("confidence"), json.dumps(kw.get("unresolved") or [], ensure_ascii=False),
        kw.get("route"), kw.get("capability"), kw.get("llm_tokens") or 0,
        kw.get("status"), kw.get("clarify_count") or 0, kw.get("answer_ms"),
    )
    try:
        with _LOCK:
            c = _conn()
            try:
                cur = c.execute(sql_, vals)
                c.commit()
                return int(cur.lastrowid or 0)
            finally:
                c.close()
    except Exception as e:
        logger.error("记录提问失败: %s", e)
        return 0


def update_question(qid: int, **fields) -> None:
    if not qid or not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [qid]
    try:
        with _LOCK:
            c = _conn()
            try:
                c.execute(f"UPDATE ai_question SET {sets} WHERE id=?", vals)
                c.commit()
            finally:
                c.close()
    except Exception as e:
        logger.error("更新提问失败: %s", e)


def record_clarification(qid: int, slot: str, asked_text: str,
                         options: Any = None, user_choice: Any = None,
                         choice_type: str = "") -> None:
    try:
        with _LOCK:
            c = _conn()
            try:
                c.execute(
                    "INSERT INTO ai_clarification(question_id,slot,asked_text,options_json,"
                    "user_choice,choice_type,created_at) VALUES(?,?,?,?,?,?,?)",
                    (qid, slot, asked_text,
                     json.dumps(options, ensure_ascii=False) if options is not None else None,
                     json.dumps(user_choice, ensure_ascii=False) if not isinstance(user_choice, (str, type(None))) else user_choice,
                     choice_type, _now()))
                c.commit()
            finally:
                c.close()
    except Exception as e:
        logger.error("记录澄清失败: %s", e)


def record_execution(qid: int, plan: dict, edited: bool = False,
                     edited_from: str = "", cache_hit: bool = False,
                     sql_ms: int = 0, row_count: int = 0) -> None:
    try:
        with _LOCK:
            c = _conn()
            try:
                c.execute(
                    "INSERT INTO ai_execution(question_id,plan_json,plan_edited,edited_from,"
                    "cache_hit,sql_ms,row_count,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (qid, json.dumps(plan, ensure_ascii=False), 1 if edited else 0,
                     edited_from, 1 if cache_hit else 0, sql_ms, row_count, _now()))
                c.commit()
            finally:
                c.close()
    except Exception as e:
        logger.error("记录执行失败: %s", e)


def record_feedback(qid: int, rating: int, comment: str = "") -> None:
    try:
        with _LOCK:
            c = _conn()
            try:
                c.execute("INSERT INTO ai_feedback(question_id,rating,comment,created_at)"
                          " VALUES(?,?,?,?)", (qid, rating, comment, _now()))
                c.commit()
            finally:
                c.close()
    except Exception as e:
        logger.error("记录反馈失败: %s", e)


# ---------- 读取（后台优化页） ----------

def recent_questions(limit: int = 50, status: str = "") -> List[dict]:
    try:
        with _LOCK:
            c = _conn()
            try:
                if status:
                    rows = c.execute(
                        "SELECT * FROM ai_question WHERE status=? ORDER BY id DESC LIMIT ?",
                        (status, limit)).fetchall()
                else:
                    rows = c.execute(
                        "SELECT * FROM ai_question ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
                return [dict(r) for r in rows]
            finally:
                c.close()
    except Exception as e:
        logger.error("读取提问失败: %s", e)
        return []


def unresolved_top(limit: int = 30) -> List[dict]:
    """没解析出的词频 Top N —— 直接指导「补哪些别名/模板」。"""
    try:
        with _LOCK:
            c = _conn()
            try:
                rows = c.execute(
                    "SELECT unresolved FROM ai_question WHERE unresolved IS NOT NULL"
                    " AND unresolved != '[]'").fetchall()
            finally:
                c.close()
    except Exception as e:
        logger.error("读取 unresolved 失败: %s", e)
        return []
    counter: Dict[str, int] = {}
    for r in rows:
        try:
            for w in json.loads(r["unresolved"] or "[]"):
                if w:
                    counter[str(w)] = counter.get(str(w), 0) + 1
        except Exception:
            continue
    return [{"word": k, "count": v} for k, v in
            sorted(counter.items(), key=lambda kv: -kv[1])[:limit]]


def slot_choice_stats(slot: str = "", limit: int = 30) -> List[dict]:
    """每个槽位的用户选择分布 —— 高频项升级为「默认建议值」，反问次数自然下降。"""
    try:
        with _LOCK:
            c = _conn()
            try:
                if slot:
                    rows = c.execute(
                        "SELECT slot, user_choice, COUNT(*) AS n FROM ai_clarification"
                        " WHERE slot=? AND user_choice IS NOT NULL GROUP BY slot,user_choice"
                        " ORDER BY n DESC LIMIT ?", (slot, limit)).fetchall()
                else:
                    rows = c.execute(
                        "SELECT slot, user_choice, COUNT(*) AS n FROM ai_clarification"
                        " WHERE user_choice IS NOT NULL GROUP BY slot,user_choice"
                        " ORDER BY n DESC LIMIT ?", (limit,)).fetchall()
                return [dict(r) for r in rows]
            finally:
                c.close()
    except Exception as e:
        logger.error("读取槽位选择失败: %s", e)
        return []


def overview() -> dict:
    """埋点总览：0 token 覆盖率、反问率、放弃率 —— 用来验证优化是否真的生效。"""
    out = {"total": 0, "by_route": {}, "by_status": {},
           "zero_token_rate": 0.0, "avg_clarify": 0.0, "llm_tokens": 0}
    try:
        with _LOCK:
            c = _conn()
            try:
                out["total"] = c.execute("SELECT COUNT(*) n FROM ai_question").fetchone()["n"]
                for r in c.execute("SELECT route, COUNT(*) n FROM ai_question"
                                   " GROUP BY route").fetchall():
                    out["by_route"][r["route"] or "-"] = r["n"]
                for r in c.execute("SELECT status, COUNT(*) n FROM ai_question"
                                   " GROUP BY status").fetchall():
                    out["by_status"][r["status"] or "-"] = r["n"]
                row = c.execute("SELECT SUM(llm_tokens) t, AVG(clarify_count) a"
                                " FROM ai_question").fetchone()
                out["llm_tokens"] = int(row["t"] or 0)
                out["avg_clarify"] = round(float(row["a"] or 0), 2)
                z = c.execute("SELECT COUNT(*) n FROM ai_question WHERE llm_tokens=0").fetchone()["n"]
                out["zero_token_rate"] = round(z / out["total"] * 100, 1) if out["total"] else 0.0
            finally:
                c.close()
    except Exception as e:
        logger.error("读取埋点总览失败: %s", e)
    return out
