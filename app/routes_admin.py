# -*- coding: utf-8 -*-
"""角色後台(T05):列出使用者、派/停角色。

用途: 讓管理員能開通別人,並讓核可條件 C2(單獨停用 `reader`)有實際的操作介面。
副作用: 讀寫 `user_role`;**不寫** `app_user` 的任何欄位。

🔴 本模組是唯一顯示 `display_name` 的地方(契約 §4.2a L1:僅供管理後台顯示)。
   三條約束跟著它:
   ① **顯示資料時間**,過期不隱藏而是標示(L1 第 3 條);
   ② 沒有快取值就顯示「—」,**不回退去查 IdP**(那會把 IdP 變成清單頁的熱路徑);
   ③ 這個值**不進 log**、不參與任何判定。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.authz import ALL_ROLES, CAP_MANAGE_ROLES, ROLE_READER, require_capability
from app.db import session_scope
from app.models import AppUser
from app.oidc import OidcError, log_event


def build_admin_router(*, settings) -> APIRouter:
    """建立角色後台路由。

    參數: settings — Settings 快照(取 base_path 組表單的 action)
    回傳: APIRouter
    副作用: 無(只組 router)
    """
    router = APIRouter(prefix="/admin", tags=["admin"])
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

    @router.get("/users", response_class=HTMLResponse, include_in_schema=False)
    def users(request: Request, identity=Depends(require_capability(CAP_MANAGE_ROLES))):
        """使用者清單 + 角色開關。

        回傳: 200 HTML
        副作用: 讀資料庫
        錯誤: 未登入 → 401;非 admin → 403
        """
        with session_scope() as db:
            rows = []
            for user in db.query(AppUser).order_by(AppUser.created_at).all():
                rows.append(
                    {
                        "sub": user.sub,
                        # 沒有快取值就是沒有——不回退去查 IdP(見模組 docstring 約束②)
                        "display_name": user.display_name or "—",
                        "display_name_updated_at": user.display_name_updated_at,
                        "is_active": user.is_active,
                        "roles": {r.role: r.enabled for r in user.roles},
                    }
                )
        return templates.TemplateResponse(
            request=request,
            name="admin_users.html",
            context={
                "rows": rows,
                "all_roles": ALL_ROLES,
                "base_path": settings.base_path,
                "auto_grant_reader": settings.auto_grant_reader,
            },
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/users/roles", include_in_schema=False)
    def set_role(
        sub: str = Form(...),
        role: str = Form(...),
        enabled: str = Form(...),
        identity=Depends(require_capability(CAP_MANAGE_ROLES)),
    ):
        """派/停某人的某個角色。

        參數: sub — 對象;role — `ALL_ROLES` 之一;enabled — "1"/"0"
        回傳: 302 回清單頁
        副作用: INSERT 或 UPDATE 一列 `user_role`
        錯誤: 未知角色 → 400

        🔴 稽核只記「誰、替誰、什麼角色、開還是關」——**不記姓名**
        (本專案紅線:log 只記 id、sub、事件類型)。
        """
        actor_sub, _roles = identity
        if role not in ALL_ROLES:
            raise OidcError(400, "unknown_role", f"未知角色:{role}")
        want = enabled == "1"

        from app.repo import grant_role, set_role_enabled

        with session_scope() as db:
            if want:
                # 先確保那一列存在,再明確啟用 —— `grant_role` 刻意不會復活
                # 已停用的角色(見其 docstring),所以這裡要兩步。
                grant_role(db, sub, role, granted_by=actor_sub)
                set_role_enabled(db, sub, role, enabled=True)
            else:
                set_role_enabled(db, sub, role, enabled=False)

        log_event("role_changed", actor=actor_sub, target=sub, role=role, enabled=want)
        return RedirectResponse(f"{settings.base_path}/admin/users", status_code=303)

    @router.post("/users/purge-display-names", include_in_schema=False)
    def purge(
        sub: str = Form(""),
        identity=Depends(require_capability(CAP_MANAGE_ROLES)),
    ):
        """清除 `display_name` 快取(留空=整批)。

        回傳: 302 回清單頁
        副作用: UPDATE `app_user`(只清快取欄,不動使用者與角色)

        契約 §4.2a L1 第 7 條要求的清除工具的 UI 入口。
        CLI 版在 `tools/purge_display_names.py`(維運不必登入後台也能清)。
        """
        actor_sub, _roles = identity
        from app.repo import purge_display_names

        with session_scope() as db:
            n = purge_display_names(db, sub=sub or None)
        log_event("display_name_purged", actor=actor_sub, scope=sub or "all", cleared=n)
        return RedirectResponse(f"{settings.base_path}/admin/users", status_code=303)

    return router
