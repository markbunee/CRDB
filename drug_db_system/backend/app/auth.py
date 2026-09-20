# -*- coding: utf-8 -*-
"""认证与授权基础设施：用户表/申请表、密码哈希、JWT、登录限流。

安全设计（面向公网部署）：
1. 密码一律 bcrypt 加盐哈希存储，**永不明文落库**；后台只显示掩码 + 「重置密码」，
   重置/新成员审批时由管理员或系统设定密码，并**仅回显一次**。
2. 令牌为 JWT(HS256)，通过 Authorization: Bearer 传递；不使用 Cookie，天然免疫 CSRF。
3. 登录失败限流（同 IP+账号 连续失败 5 次锁定 5 分钟），防暴力破解。
4. 管理员禁用成员后，令牌在状态缓存 TTL（默认 60s）内失效，无需等令牌自然过期。
5. AUTH_SECRET_KEY 必须显式配置；未配置时生成随机值并告警（重启后所有登录态失效）。

用户体系存放在 PG_ADMIN_DB（默认 postgres）中的 crdb_users / crdb_applications 表，
与三个业务库解耦，业务数据清空不会连累账号。
"""
import os
import json
import time
import logging
import secrets
import threading
import string
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import bcrypt
import jwt

from .database import auth_conn

logger = logging.getLogger("app.auth")

# ---------- 配置 ----------
_AUTH_SECRET = os.environ.get("AUTH_SECRET_KEY", "").strip()
if not _AUTH_SECRET:
    _AUTH_SECRET = secrets.token_urlsafe(48)
    logger.warning(
        "未设置环境变量 AUTH_SECRET_KEY，已生成临时随机密钥：服务重启后所有登录态会失效。"
        "公网部署务必显式配置 AUTH_SECRET_KEY！"
    )
AUTH_SECRET_KEY = _AUTH_SECRET

ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))
BCRYPT_ROUNDS = int(os.environ.get("BCRYPT_ROUNDS", "12"))

ADMIN_USERNAME = os.environ.get("ADMIN_INITIAL_USERNAME", "admin").strip() or "admin"
ADMIN_PASSWORD = os.environ.get("ADMIN_INITIAL_PASSWORD", "").strip()
ADMIN_DISPLAY_NAME = os.environ.get("ADMIN_INITIAL_NAME", "系统管理员").strip() or "系统管理员"
ADMIN_PHONE = os.environ.get("ADMIN_INITIAL_PHONE", "").strip()

ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
STATUS_ACTIVE = "active"
STATUS_DISABLED = "disabled"

# 直接使用 bcrypt 原生 API：passlib 1.7.4 与 bcrypt>=4.1 存在兼容问题，
# 且其内部 bug 探测逻辑会在新版本 bcrypt 上抛异常，故不引入该中间层。
_BCRYPT_MAX_BYTES = 72  # bcrypt 算法硬限制

# ---------- 令牌 ----------


def create_access_token(user: Dict[str, Any]) -> str:
    now = datetime.now(timezone.utc)
    # 管理员默认拥有全部权限；成员使用其被显式授予的权限
    perms = ["import", "export"] if user.get("role") == ROLE_ADMIN else (user.get("permissions") or [])
    payload = {
        "sub": str(user["id"]),
        "username": user["username"],
        "role": user["role"],
        "name": user.get("display_name") or user["username"],
        "permissions": perms,
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, AUTH_SECRET_KEY, algorithm="HS256")


def decode_token(token: str) -> Optional[Dict[str, Any]]:
    """解析令牌，失败（过期/被篡改/签名不符）返回 None。"""
    try:
        return jwt.decode(token, AUTH_SECRET_KEY, algorithms=["HS256"])
    except Exception:
        return None


# ---------- 密码 ----------


def hash_password(plain: str) -> str:
    """bcrypt 加盐哈希。返回可直接入库的字符串。"""
    data = plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(data, bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """校验密码。任何异常（哈希损坏/超长/格式不符）一律视为校验失败。"""
    try:
        return bcrypt.checkpw(
            plain.encode("utf-8")[:_BCRYPT_MAX_BYTES], hashed.encode("utf-8")
        )
    except Exception:
        return False


def generate_password(length: int = 12) -> str:
    """生成随机临时密码（含大小写字母+数字，去掉易混淆字符）。"""
    alphabet = string.ascii_letters + string.digits
    for ch in "0O1lI":
        alphabet = alphabet.replace(ch, "")
    return "".join(secrets.choice(alphabet) for _ in range(length))


def validate_password_strength(pwd: str) -> Optional[str]:
    """弱口令校验：返回 None 表示通过，否则返回错误提示。"""
    if len(pwd) < 8:
        return "密码长度至少 8 位"
    if len(pwd.encode("utf-8")) > _BCRYPT_MAX_BYTES:
        return f"密码过长（最多 {_BCRYPT_MAX_BYTES} 个字符）"
    if pwd.isdigit() or pwd.isalpha():
        return "密码需同时包含字母和数字"
    return None


# ---------- 登录限流 ----------
_LOGIN_MAX_FAIL = 5
_LOGIN_BLOCK_SECONDS = 300
_login_fail: Dict[str, List[Any]] = {}
_login_lock = threading.Lock()


def login_lock_remaining(key: str) -> int:
    """返回剩余锁定秒数；0 表示未被锁定。"""
    with _login_lock:
        rec = _login_fail.get(key)
        if not rec:
            return 0
        count, first_ts = rec
        if count < _LOGIN_MAX_FAIL:
            return 0
        left = int(_LOGIN_BLOCK_SECONDS - (time.time() - first_ts))
        if left <= 0:
            del _login_fail[key]
            return 0
        return left


def record_login_fail(key: str):
    with _login_lock:
        rec = _login_fail.get(key)
        if rec and time.time() - rec[1] > _LOGIN_BLOCK_SECONDS:
            rec = None
        if rec:
            rec[0] += 1
        else:
            _login_fail[key] = [1, time.time()]


def clear_login_fail(key: str):
    with _login_lock:
        _login_fail.pop(key, None)


# ---------- 数据表 ----------

_CREATE_USERS = """
CREATE TABLE IF NOT EXISTS crdb_users (
    id BIGSERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    phone TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    status TEXT NOT NULL DEFAULT 'active',
    must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
    permissions TEXT NOT NULL DEFAULT '[]',
    last_login_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_crdb_users_role ON crdb_users(role);
"""

_CREATE_APPLICATIONS = """
CREATE TABLE IF NOT EXISTS crdb_applications (
    id BIGSERIAL PRIMARY KEY,
    display_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    username TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at TIMESTAMPTZ,
    reviewed_by TEXT,
    reject_reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_crdb_applications_status ON crdb_applications(status);
"""

_USER_COLS = (
    "id, username, display_name, phone, role, status, must_change_password, "
    "permissions, last_login_at, created_at, updated_at"
)


def ensure_auth_tables() -> None:
    """幂等建表 + 确保默认管理员存在。返回是否新创建了管理员。"""
    with auth_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_CREATE_USERS)
            # 旧表可能缺少 permissions 列，幂等补列（默认值空数组）
            cur.execute(
                "ALTER TABLE crdb_users ADD COLUMN IF NOT EXISTS "
                "permissions TEXT NOT NULL DEFAULT '[]'"
            )
            cur.execute(_CREATE_APPLICATIONS)
            cur.execute("SELECT COUNT(*) FROM crdb_users")
            (count,) = cur.fetchone()
            if count:
                return
            pwd = ADMIN_PASSWORD or generate_password()
            cur.execute(
                "INSERT INTO crdb_users (username, display_name, phone, password_hash, role, status)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (ADMIN_USERNAME, ADMIN_DISPLAY_NAME, ADMIN_PHONE, hash_password(pwd), ROLE_ADMIN, STATUS_ACTIVE),
            )
        if not ADMIN_PASSWORD:
            # 管理员初始口令只打印一次到日志，不落库明文
            logger.warning(
                "已创建默认管理员账号：%s，初始密码：%s（仅此一次显示，请登录后立即修改）",
                ADMIN_USERNAME, pwd,
            )
        else:
            logger.info("已创建默认管理员账号：%s（使用 ADMIN_INITIAL_PASSWORD 指定口令）", ADMIN_USERNAME)


# ---------- 用户读写 ----------


def _parse_permissions(raw: Any) -> List[str]:
    """把库里的 permissions（JSON 文本/列表/None）统一解析为字符串列表。"""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(p) for p in raw]
    try:
        val = json.loads(raw)
        return [str(p) for p in val] if isinstance(val, list) else []
    except Exception:
        return []


def _row_to_user(row) -> Dict[str, Any]:
    d = dict(zip(_USER_COLS.replace(" ", "").split(","), row))
    last = d.get("last_login_at")
    d["last_login_at"] = last.isoformat() if last else None
    created = d.get("created_at")
    d["created_at"] = created.isoformat() if created else None
    d["permissions"] = _parse_permissions(d.get("permissions"))
    return d


def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    with auth_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {_USER_COLS} FROM crdb_users WHERE username = %s", (username,))
            row = cur.fetchone()
    return _row_to_user(row) if row else None


def get_user_auth(username: str) -> Optional[Dict[str, Any]]:
    """登录专用：额外取回 password_hash（仅内存中使用，绝不外传）。"""
    with auth_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_USER_COLS}, password_hash FROM crdb_users WHERE username = %s",
                (username,),
            )
            row = cur.fetchone()
    if not row:
        return None
    d = _row_to_user(row[:-1])
    d["password_hash"] = row[-1]
    return d


def get_user_by_id(uid: int) -> Optional[Dict[str, Any]]:
    with auth_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {_USER_COLS} FROM crdb_users WHERE id = %s", (uid,))
            row = cur.fetchone()
    return _row_to_user(row) if row else None


def list_users() -> List[Dict[str, Any]]:
    with auth_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_USER_COLS} FROM crdb_users ORDER BY "
                "CASE role WHEN 'admin' THEN 0 ELSE 1 END, id"
            )
            rows = cur.fetchall()
    return [_row_to_user(r) for r in rows]


def touch_login(uid: int) -> None:
    with auth_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE crdb_users SET last_login_at = now(), updated_at = now() WHERE id = %s",
                (uid,),
            )


def create_user(
    username: str, display_name: str, phone: str, password: str,
    role: str = ROLE_MEMBER, permissions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    perms = permissions or []
    with auth_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO crdb_users (username, display_name, phone, password_hash, role, status,"
                " must_change_password, permissions) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (username, display_name, phone, hash_password(password), role, STATUS_ACTIVE, True,
                 json.dumps(perms)),
            )
            (uid,) = cur.fetchone()
    return get_user_by_id(uid)


def set_user_permissions(uid: int, permissions: List[str]) -> None:
    """设置成员被显式授予的权限列表（管理员无需调用，默认拥有全部权限）。"""
    with auth_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE crdb_users SET permissions = %s, updated_at = now() WHERE id = %s",
                (json.dumps(permissions), uid),
            )


def set_password(uid: int, new_password: str, must_change: bool = True) -> None:
    with auth_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE crdb_users SET password_hash = %s, must_change_password = %s, updated_at = now()"
                " WHERE id = %s",
                (hash_password(new_password), must_change, uid),
            )


def set_user_status(uid: int, status: str) -> None:
    with auth_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE crdb_users SET status = %s, updated_at = now() WHERE id = %s",
                (status, uid),
            )
    _status_cache.pop(uid, None)


# ---------- 用户状态缓存（让“禁用”及时生效）----------
_status_cache: Dict[int, Any] = {}
_status_lock = threading.Lock()
_STATUS_TTL = int(os.environ.get("AUTH_STATUS_CACHE_TTL", "60"))


def get_user_status(uid: int, fallback: str = STATUS_ACTIVE) -> str:
    now = time.time()
    with _status_lock:
        rec = _status_cache.get(uid)
        if rec and now - rec[1] < _STATUS_TTL:
            return rec[0]
    try:
        with auth_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM crdb_users WHERE id = %s", (uid,))
                row = cur.fetchone()
        status = row[0] if row else None
    except Exception:
        status = None
    if status is None:
        status = fallback
    with _status_lock:
        _status_cache[uid] = (status, now)
    return status


# ---------- 申请表 ----------

_APP_COLS = (
    "id, display_name, phone, username, note, status, created_at, reviewed_at, reviewed_by, reject_reason"
)


def _row_to_app(row) -> Dict[str, Any]:
    d = dict(zip(_APP_COLS.replace(" ", "").split(","), row))
    for k in ("created_at", "reviewed_at"):
        v = d.get(k)
        d[k] = v.isoformat() if v else None
    return d


def create_application(display_name: str, phone: str, username: str = "", note: str = "") -> Dict[str, Any]:
    with auth_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO crdb_applications (display_name, phone, username, note)"
                " VALUES (%s, %s, %s, %s) RETURNING id",
                (display_name, phone, username, note),
            )
            (aid,) = cur.fetchone()
    return get_application(aid)


def get_application(aid: int) -> Optional[Dict[str, Any]]:
    with auth_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {_APP_COLS} FROM crdb_applications WHERE id = %s", (aid,))
            row = cur.fetchone()
    return _row_to_app(row) if row else None


def list_applications(status: str = "") -> List[Dict[str, Any]]:
    sql = f"SELECT {_APP_COLS} FROM crdb_applications"
    params: List[Any] = []
    if status:
        sql += " WHERE status = %s"
        params.append(status)
    sql += " ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, id DESC"
    with auth_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    return [_row_to_app(r) for r in rows]


def update_application_status(aid: int, status: str, reviewer: str, reason: str = "") -> None:
    with auth_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE crdb_applications SET status = %s, reviewed_at = now(), reviewed_by = %s,"
                " reject_reason = %s WHERE id = %s",
                (status, reviewer, reason, aid),
            )
