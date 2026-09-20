# -*- coding: utf-8 -*-
"""认证与成员管理接口：登录、账号申请、管理员审批、成员管理。

权限模型：
- 管理员(admin)：导入 Excel / 导出数据 / 删除数据 / 后台成员管理 / 审批申请。
- 成员(member)：仅可查询数据与查看统计、看板；敏感操作前端拦截 + 后端 403 双保险。

安全要点（公网部署）：
- 密码 bcrypt 哈希存储，任何接口都不会返回明文或哈希；
- 管理员给新成员设的初始密码、重置后的临时密码**仅在响应里回显一次**；
- 登录接口同 IP+账号连续失败 5 次锁定 5 分钟。
"""
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from .. import auth as A
from ..dependencies import get_current_user, require_admin

logger = logging.getLogger("app.routers.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _public_user(u: Dict[str, Any]) -> Dict[str, Any]:
    """用户对外输出：剔除 password_hash，密码字段只给掩码。"""
    d = {k: v for k, v in u.items() if k != "password_hash"}
    d["password_mask"] = "••••••"
    return d


# ---------- 登录 / 登出 / 当前用户 ----------


@router.post("/login")
def login(request: Request, body: Dict[str, Any] = Body(...)):
    """账号密码登录，成功返回 JWT。"""
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    if not username or not password:
        raise HTTPException(400, "请输入账号和密码")

    ip = request.client.host if request.client else "unknown"
    key = f"{ip}|{username}"
    left = A.login_lock_remaining(key)
    if left > 0:
        raise HTTPException(429, f"登录失败次数过多，请 {left} 秒后再试")

    user = A.get_user_auth(username)
    if not user or not A.verify_password(password, user["password_hash"]):
        A.record_login_fail(key)
        logger.warning("登录失败（账号或密码错误）: %s from %s", username, ip)
        raise HTTPException(401, "账号或密码错误")
    if user["status"] != A.STATUS_ACTIVE:
        raise HTTPException(403, "账号已被禁用，请联系管理员")

    A.clear_login_fail(key)
    A.touch_login(user["id"])
    token = A.create_access_token(user)
    logger.info("用户登录成功: %s（角色 %s）", username, user["role"])
    return {"token": token, "user": _public_user(user)}


@router.post("/logout")
def logout(user: dict = Depends(get_current_user)):
    """登出。JWT 无状态，服务端无需回收，前端丢弃令牌即可。"""
    return {"message": "已登出"}


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    """返回当前登录用户完整信息（含角色、是否需改密码、上次登录时间）。"""
    u = A.get_user_by_id(user["id"])
    if not u:
        raise HTTPException(401, "账号不存在或已被删除")
    return _public_user(u)


@router.post("/change-password")
def change_password(user: dict = Depends(get_current_user), body: Dict[str, Any] = Body(...)):
    """修改自己的密码。"""
    old = str(body.get("old_password") or "")
    new = str(body.get("new_password") or "")
    if not old or not new:
        raise HTTPException(400, "请输入原密码和新密码")
    rec = A.get_user_auth(user["username"])
    if not rec or not A.verify_password(old, rec["password_hash"]):
        raise HTTPException(400, "原密码不正确")
    weak = A.validate_password_strength(new)
    if weak:
        raise HTTPException(400, weak)
    A.set_password(user["id"], new, must_change=False)
    logger.info("用户修改密码成功: %s", user["username"])
    return {"message": "密码修改成功"}


# ---------- 新成员申请（无需登录）----------


@router.post("/apply")
def apply(request: Request, body: Dict[str, Any] = Body(...)):
    """提交入会申请：姓名 + 电话（必填），可附登录账号与备注。

    提交后状态为 pending，需管理员在后台同意后才能登录。
    """
    name = str(body.get("display_name") or body.get("name") or "").strip()
    phone = str(body.get("phone") or "").strip()
    username = str(body.get("username") or "").strip()
    note = str(body.get("note") or "").strip()
    if not name:
        raise HTTPException(400, "请填写姓名")
    if not phone:
        raise HTTPException(400, "请填写联系电话")
    if len(phone) < 6:
        raise HTTPException(400, "联系电话格式不正确")

    # 同一手机号已有待审申请则不重复提交
    for app_ in A.list_applications("pending"):
        if app_["phone"] == phone:
            raise HTTPException(400, "该手机号已有待审核的申请，请耐心等待管理员处理")

    rec = A.create_application(name, phone, username, note)
    logger.info("收到新成员申请: %s / %s", name, phone)
    return {"message": "申请已提交，请等待管理员审核", "application": rec}


# ---------- 后台：成员管理（仅管理员）----------


@router.get("/members")
def list_members(_admin: dict = Depends(require_admin)):
    """后台成员列表：人数统计 + 每个成员的姓名/账号/电话/角色/状态/最新上线时间。

    密码以掩码返回（库里只有 bcrypt 哈希，无法也不应反推明文）。
    """
    users = A.list_users()
    pending = len(A.list_applications("pending"))
    return {
        "total": len(users),
        "admin_count": sum(1 for u in users if u["role"] == A.ROLE_ADMIN),
        "member_count": sum(1 for u in users if u["role"] == A.ROLE_MEMBER),
        "pending_applications": pending,
        "users": [_public_user(u) for u in users],
    }


@router.post("/members/{uid}/reset-password")
def reset_password(
    uid: int,
    _admin: dict = Depends(require_admin),
    body: Optional[Dict[str, Any]] = Body(None),
):
    """管理员重置成员密码。

    body 传 {password: "..."} 则使用指定密码；不传则由系统生成随机强密码。
    新密码**仅在本次响应中回显一次**，请管理员转告成员并提醒其尽快修改。
    """
    user = A.get_user_by_id(uid)
    if not user:
        raise HTTPException(404, "成员不存在")
    if user["role"] == A.ROLE_ADMIN:
        raise HTTPException(400, "不允许重置其他管理员的密码，请使用「修改密码」自行操作")

    body = body or {}
    pwd = str(body.get("password") or "").strip()
    if not pwd:
        pwd = A.generate_password()
    else:
        weak = A.validate_password_strength(pwd)
        if weak:
            raise HTTPException(400, weak)

    A.set_password(uid, pwd, must_change=True)
    logger.info("管理员 %s 重置了成员 %s 的密码", _admin.get("username"), user["username"])
    return {"message": "密码已重置", "username": user["username"], "new_password": pwd}


@router.post("/members/{uid}/status")
def set_member_status(
    uid: int,
    _admin: dict = Depends(require_admin),
    body: Dict[str, Any] = Body(...),
):
    """启用 / 禁用成员。禁用后其令牌最多 60 秒内失效，无法继续访问。"""
    user = A.get_user_by_id(uid)
    if not user:
        raise HTTPException(404, "成员不存在")
    status = str(body.get("status") or "").strip()
    if status not in (A.STATUS_ACTIVE, A.STATUS_DISABLED):
        raise HTTPException(400, "status 只能是 active 或 disabled")
    if user["role"] == A.ROLE_ADMIN and status == A.STATUS_DISABLED:
        raise HTTPException(400, "不能禁用管理员账号")
    A.set_user_status(uid, status)
    logger.info("管理员 %s 将成员 %s 状态改为 %s", _admin.get("username"), user["username"], status)
    return {"message": "状态已更新", "username": user["username"], "status": status}


@router.post("/members/{uid}/permissions")
def set_member_permissions(
    uid: int,
    _admin: dict = Depends(require_admin),
    body: Dict[str, Any] = Body(...),
):
    """管理员为成员配置导入/导出权限（按用户开放）。管理员本身默认拥有全部权限。"""
    user = A.get_user_by_id(uid)
    if not user:
        raise HTTPException(404, "成员不存在")
    if user["role"] == A.ROLE_ADMIN:
        raise HTTPException(400, "管理员默认拥有全部权限，无需单独设置")
    perms = body.get("permissions")
    if not isinstance(perms, list):
        raise HTTPException(400, "permissions 必须是字符串数组，如 [\"import\", \"export\"]")
    allowed = {"import", "export"}
    perms = [str(p) for p in perms if str(p) in allowed]
    A.set_user_permissions(uid, perms)
    logger.info("管理员 %s 调整成员 %s 权限为 %s", _admin.get("username"), user["username"], perms)
    return {"message": "权限已更新", "username": user["username"], "permissions": perms}


# ---------- 后台：申请审批（仅管理员）----------


@router.get("/applications")
def list_apps(status: str = "", _admin: dict = Depends(require_admin)):
    """申请列表；status 可传 pending / approved / rejected，不传返回全部。"""
    return {"applications": A.list_applications(status)}


@router.post("/applications/{aid}/approve")
def approve_application(
    aid: int,
    _admin: dict = Depends(require_admin),
    body: Optional[Dict[str, Any]] = Body(None),
):
    """同意申请：创建成员账号并设定初始密码（**仅回显一次**）。

    body: {username?: str, password?: str}
    - username 不传则用申请时填写的账号，仍没有则按手机号自动生成；
    - password 不传则由系统生成随机强密码。
    """
    app_ = A.get_application(aid)
    if not app_:
        raise HTTPException(404, "申请不存在")
    if app_["status"] != "pending":
        raise HTTPException(400, f"该申请已处理（{app_['status']}），无法重复审批")

    body = body or {}
    username = str(body.get("username") or app_.get("username") or "").strip()
    if not username:
        username = "u" + "".join(ch for ch in app_["phone"] if ch.isdigit())[-8:]
    if A.get_user_by_username(username):
        raise HTTPException(400, f"登录账号 {username} 已存在，请换一个")

    pwd = str(body.get("password") or "").strip()
    if not pwd:
        pwd = A.generate_password()
    else:
        weak = A.validate_password_strength(pwd)
        if weak:
            raise HTTPException(400, weak)

    user = A.create_user(
        username=username,
        display_name=app_["display_name"],
        phone=app_["phone"],
        password=pwd,
        role=A.ROLE_MEMBER,
    )
    A.update_application_status(aid, "approved", _admin.get("username", ""))
    logger.info("管理员 %s 同意了 %s 的申请，生成账号 %s", _admin.get("username"), app_["display_name"], username)
    return {
        "message": "已同意申请，账号创建成功",
        "user": _public_user(user),
        "initial_password": pwd,  # 唯一一次明文展示
    }


@router.post("/applications/{aid}/reject")
def reject_application(
    aid: int,
    _admin: dict = Depends(require_admin),
    body: Optional[Dict[str, Any]] = Body(None),
):
    """拒绝申请。"""
    app_ = A.get_application(aid)
    if not app_:
        raise HTTPException(404, "申请不存在")
    if app_["status"] != "pending":
        raise HTTPException(400, "该申请已处理，无法重复审批")
    body = body or {}
    reason = str(body.get("reason") or "").strip()
    A.update_application_status(aid, "rejected", _admin.get("username", ""), reason)
    return {"message": "已拒绝该申请"}
