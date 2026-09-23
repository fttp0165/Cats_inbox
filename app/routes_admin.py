# -*- coding: utf-8 -*-
"""管理後台:角色(T05)與推送來源登記(T14a)。

用途: 讓管理員能開通別人,並讓核可條件 C2(單獨停用 `reader`)有實際的操作介面;
      T14a 起也在這裡登記/停用推送來源(我方答覆 portal Q2 的「管理後台加一列」)。
副作用: 讀寫 `user_role`、`source_app`;**不寫** `app_user` 的任何欄位。

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

from app.authz import (
    ALL_ROLES,
    CAP_MANAGE_ROLES,
    CAP_MANAGE_SOURCES,
    ROLE_READER,
    require_capability,
)
from app.csrf import csrf_token_for, require_csrf
from app.db import session_scope
from app.models import SOURCE_LABEL_MAX, AppUser
from app.oidc import OidcError, log_event
from app.s2s import SCOPE_NOTIFICATION_PUSH
from app.validation import BadRequest, NotFound, iso_utc


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
                # 🔴 T10b:這一頁的兩個表單都要帶 CSRF token。
                #    後端驗了而模板忘了放,症狀是**那個按鈕從此無效**,
                #    而它回 403 —— 看起來像權限問題,查錯方向。
                "csrf_token": csrf_token_for(request),
            },
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/users/roles", include_in_schema=False)
    def set_role(
        sub: str = Form(...),
        role: str = Form(...),
        enabled: str = Form(...),
        _csrf=Depends(require_csrf),
        identity=Depends(require_capability(CAP_MANAGE_ROLES)),
    ):
        """派/停某人的某個角色。

        參數: sub — 對象;role — `ALL_ROLES` 之一;enabled — "1"/"0"
        回傳: 302 回清單頁
        副作用: INSERT 或 UPDATE 一列 `user_role`
        錯誤: 未知角色 → 400

        🔴 稽核只記「誰、替誰、什麼角色、開還是關」——**不記姓名**
        (本專案紅線:log 只記 id、sub、事件類型)。

        🔴 **CSRF 由 `Depends(require_csrf)` 在進到這裡之前驗完(T10b)。**
           在此之前這個表單**沒有 CSRF** —— 它從 T05 就在,而那時本專案
           還沒有任何 CSRF 機制。攻擊者能誘使已登入的管理員開一個網頁,
           就**替自己開通任何角色**,而管理員看到的畫面完全正常。
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
        _csrf=Depends(require_csrf),
        identity=Depends(require_capability(CAP_MANAGE_ROLES)),
    ):
        """清除 `display_name` 快取(留空=整批)。

        回傳: 302 回清單頁
        副作用: UPDATE `app_user`(只清快取欄,不動使用者與角色)

        契約 §4.2a L1 第 7 條要求的清除工具的 UI 入口。
        CLI 版在 `tools/purge_display_names.py`(維運不必登入後台也能清)。

        🔴 **CSRF 由 `Depends(require_csrf)` 驗(T10b)。** 留空 `sub` 就是
           **整批清除**,所以這個表單被跨站觸發的後果是整欄快取消失 ——
           而成功時只是一個 303,管理員下一次看清單才會發現。
        """
        actor_sub, _roles = identity
        from app.repo import purge_display_names

        with session_scope() as db:
            n = purge_display_names(db, sub=sub or None)
        log_event("display_name_purged", actor=actor_sub, scope=sub or "all", cleared=n)
        return RedirectResponse(f"{settings.base_path}/admin/users", status_code=303)

    # ══════════════════════════════════════════════════════════════════
    # T14a:推送來源登記表
    #
    # 🔴 這一頁決定「**誰能以平台的名義發通知給任何人**」。登記一個 `azp`,
    #    等於讓那個 client 的每一張帶 `notification:push` 的 token 都能推 ——
    #    所以能力是 admin 專屬的 `manage_sources`,且兩個 POST 都走 CSRF。
    # ══════════════════════════════════════════════════════════════════
    @router.get("/sources", response_class=HTMLResponse, include_in_schema=False)
    def sources(request: Request, identity=Depends(require_capability(CAP_MANAGE_SOURCES))):
        """來源清單 + 登記表單 + 啟用/停用。

        回傳: 200 HTML
        副作用: 讀資料庫
        錯誤: 未登入 → 401;非 admin → 403
        """
        from app.repo import list_source_apps

        with session_scope() as db:
            rows = [
                {
                    "azp": s.azp,
                    "label": s.label,
                    "enabled": s.enabled,
                    # 登記者只存、只顯示 sub(稽核資訊);姓名快取只在角色後台那一頁出現
                    "created_by": s.created_by,
                    "created_at": iso_utc(s.created_at),
                }
                for s in list_source_apps(db)
            ]
        return templates.TemplateResponse(
            request=request,
            name="admin_sources.html",
            context={
                "rows": rows,
                "base_path": settings.base_path,
                "scope": SCOPE_NOTIFICATION_PUSH,
                "label_max": SOURCE_LABEL_MAX,
                "own_client_id": settings.oidc_client_id,
                "csrf_token": csrf_token_for(request),
            },
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/sources", include_in_schema=False)
    def register_source(
        azp: str = Form(""),
        label: str = Form(""),
        _csrf=Depends(require_csrf),
        identity=Depends(require_capability(CAP_MANAGE_SOURCES)),
    ):
        """登記一個推送來源。

        參數: azp — 呼叫方的 client_id;label — 收件人看到的「寄件人」標籤
        回傳: 303 回來源頁
        副作用: INSERT 一列 `source_app`
        錯誤: 值不合法 / 已登記 / 是我方自己的 client_id → 400(零列)

        🔴 **我方自己的 client_id 不能登記。** 它簽出的 token 一律是**使用者**的
           (我方 client 的 service account 已停用),在推送端點一律 401 ——
           登記了也永遠推不進來,只會讓人以為設定好了。
        ⚠ 欄位用 `Form("")` 而不是 `Form(...)`:缺欄位要回我方的 400(一致的錯誤形狀),
          不是 FastAPI 預設的 422。
        """
        actor_sub, _roles = identity
        from app.repo import register_source_app

        if azp.strip() == settings.oidc_client_id:
            raise BadRequest("reserved_azp", "不能登記本服務自己的 client_id")
        with session_scope() as db:
            row = register_source_app(db, azp=azp, label=label, created_by=actor_sub)
            registered = row.azp
        # 稽核只記「誰、登記了哪個 azp」—— 標籤是系統名,但一律不記內容型的欄位
        log_event("source_app_registered", actor=actor_sub, azp=registered)
        return RedirectResponse(f"{settings.base_path}/admin/sources", status_code=303)

    @router.post("/sources/enabled", include_in_schema=False)
    def toggle_source(
        azp: str = Form(""),
        enabled: str = Form(""),
        _csrf=Depends(require_csrf),
        identity=Depends(require_capability(CAP_MANAGE_SOURCES)),
    ):
        """啟用/停用一個推送來源(**即時生效**)。

        參數: azp;enabled — "1" 啟用 / 其他值停用
        回傳: 303 回來源頁
        副作用: UPDATE 一列 `source_app`
        錯誤: 找不到 → 404

        🔴 停用**即時生效**:`app/s2s.py` 每一次請求都查登記表、不快取。
           停用最需要的時刻,正是某個來源開始亂推的時候 —— 那時候不能等重啟。
        """
        actor_sub, _roles = identity
        from app.repo import set_source_app_enabled

        want = enabled == "1"
        with session_scope() as db:
            found = set_source_app_enabled(db, azp=azp, enabled=want)
        if not found:
            raise NotFound("來源不存在")
        log_event("source_app_toggled", actor=actor_sub, azp=azp.strip(), enabled=want)
        return RedirectResponse(f"{settings.base_path}/admin/sources", status_code=303)

    return router
