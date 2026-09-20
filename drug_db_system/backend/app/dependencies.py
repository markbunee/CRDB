# -*- coding: utf-8 -*-
"""公共依赖：校验 db_key、提供 cfg，以及登录态/管理员权限依赖。"""
from fastapi import Depends, HTTPException, Path, Request

from .models.schema_def import SCHEMAS, get_cfg


def validate_db_key(db_key: str = Path(..., description="数据库 key")):
    """路径参数校验：db_key 必须是已知的三库之一。"""
    if db_key not in SCHEMAS:
        raise HTTPException(404, f"未知数据库: {db_key}")
    return db_key


def get_db_cfg(db_key: str):
    """获取数据库配置 dict。"""
    return get_cfg(db_key)


def get_current_user(request: Request) -> dict:
    """取当前登录用户。

    用户信息由 main.py 的 auth_middleware 校验 JWT 后写入 request.state.user；
    此处只做读取，中间件已保证令牌有效且账号未被禁用。
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "未登录或登录已失效")
    return user


def require_admin(request: Request) -> dict:
    """要求管理员权限。

    用于删除数据 / 品类映射维护 / 成员管理等高危操作。成员访问直接 403，
    与前端的「无权限」提示互为纵深防御（前端拦体验，后端保安全）。
    """
    user = get_current_user(request)
    if user.get("role") != "admin":
        raise HTTPException(403, "无权限：该操作仅管理员可用")
    return user


def require_permission(perm: str):
    """细粒度权限依赖：允许管理员或拥有该权限的成员。

    perm 取值：'import'（导入/上传数据）、'export'（导出数据）。
    普通成员需由管理员在后台显式授予对应权限，否则 403（前端弹「无权限」）。
    """
    def dep(user: dict = Depends(get_current_user)) -> dict:
        perms = user.get("permissions") or []
        if user.get("role") == "admin" or perm in perms:
            return user
        raise HTTPException(403, f"无权限：需要 {perm} 权限")
    return dep
