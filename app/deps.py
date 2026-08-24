# -*- coding: utf-8 -*-
"""共用相依:解析 session(含主動續期)、把它接到本地角色。

用途: 讓任何路由用一行 `Depends(...)` 拿到「這個人是誰、他有什麼角色」。
副作用: 可能對 IdP 發 refresh 請求並更新 session;讀資料庫取角色。

🔴 **為什麼 session 解析要放在這裡,而不是留在 `routes_auth` 的 closure 裡:**
   契約 §3.3 的主動續期必須發生在**每一個**已認證請求上。留在 closure 裡
   只有 `/me` 用得到,業務端點(T08 起)會各自寫一份——而少寫的那一個
   就是「登入 5 分鐘後這個頁面莫名變未登入」,**而伺服器零錯誤**。
   抽成一處之後,忘記接的症狀是 401(明顯),不是靜默失效。

🔴 **為什麼角色每次請求都查,不快取在 session 裡:**
   管理員在後台停用一個角色之後,那個人手上的 session 還活著。
   把角色快取進 session 等於「停用要等他重新登入才生效」——
   而他不會重新登入,他會就這樣繼續用下去,**而畫面上完全正常**。
"""

from __future__ import annotations

from fastapi import Request

from app.db import session_scope
from app.oidc import OidcError, log_event
from app.session import REFRESH_MARGIN_SECONDS, SESSION_COOKIE, SessionData


def _session_from_tokens(oidc, clock, tokens: dict, *, previous: SessionData) -> SessionData:
    """把續期回來的 token 轉成新的 SessionData(不比對 nonce)。

    參數: previous — 舊 session,用來延續 sub/sid 並在 IdP 沒給新值時沿用
    回傳: SessionData
    副作用: 會驗 id_token(可能拋 OidcError)
    規範上 refresh 換來的 id_token **不帶 nonce**,硬要比會讓續期永遠失敗。

    ⚠ 刻意**不**把 `name` claim 帶進 session:L1 快取只在**登入當下**由
    `repo.ensure_user_on_login` 寫一次。續期時再寫一次不會更新(值一樣),
    卻會多開一條寫入路徑——而「僅得自本人登入 token」這句話的可驗證性
    就靠「寫入路徑只有一條」。
    """
    access = tokens.get("access_token", "")
    id_token = tokens.get("id_token", "")
    claims = oidc.verify_id_token(id_token, nonce=None, access_token=access or None)
    return SessionData(
        sub=claims["sub"],
        idp_sid=claims.get("sid") or previous.idp_sid,
        access_token=access,
        refresh_token=tokens.get("refresh_token") or previous.refresh_token,
        id_token=id_token,
        access_expires_at=clock() + float(tokens.get("expires_in", 300)),
        created_at=previous.created_at,
    )


def resolve_session(request: Request) -> tuple[str, SessionData]:
    """取當前 session,必要時主動續期(契約 §3.3)。

    回傳: (session_key, SessionData)
    副作用: 可能對 IdP 發 refresh、更新 session 儲存
    錯誤: 未登入或續期失敗 → OidcError(401)
    """
    store = request.app.state.session_store
    oidc = request.app.state.oidc
    clock = request.app.state.clock

    key = store.unseal(request.cookies.get(SESSION_COOKIE))
    data = store.get(key)
    if data is None:
        raise OidcError(401, "not_authenticated", "無有效 session")

    if data.access_expires_at - clock() > REFRESH_MARGIN_SECONDS:
        return key, data

    if not data.refresh_token:
        store.delete(key)
        raise OidcError(401, "session_expired", "access token 已過期且無 refresh_token")

    try:
        tokens = oidc.refresh(data.refresh_token)
    except OidcError as exc:
        # 🔴 refresh 失敗 = IdP 那邊 session 沒了(帳號停用、逾時、被登出)。
        # 這一刻必須登出;沿用舊 session 會讓 §3.3 換來的收權即時性變成假的。
        store.delete(key)
        log_event("oidc_refresh_failed", sub=data.sub, error=exc.code)
        raise OidcError(401, "session_expired", "refresh 失敗,已登出")

    refreshed = _session_from_tokens(oidc, clock, tokens, previous=data)
    store.replace(key, refreshed)
    log_event("oidc_refreshed", sub=refreshed.sub)
    return key, refreshed


def current_identity(request: Request) -> tuple[str, list[str]]:
    """回傳 (sub, 啟用中的角色清單)。

    回傳: (str, list[str])
    副作用: 續期(見 `resolve_session`)+ 讀資料庫取角色
    錯誤: 未登入 → 401;本地查無此人 → 401

    ⚠ 這裡刻意**不**做能力判定——判定在 `app.authz.require_capability`。
    分開的理由:未登入(401)與無權(403)必須是兩個不同的答案,
    寫在同一個函式裡遲早會被合併成同一個回傳值。
    """
    from app.repo import get_user

    _key, data = resolve_session(request)
    with session_scope() as db:
        user = get_user(db, data.sub)
        if user is None:
            # session 有效但本地查無此人:帳號被刪掉了。當成未認證處理,
            # 讓他重新登入時走一次首登流程。
            raise OidcError(401, "not_authenticated", "本地查無此身分")
        return user.sub, user.active_roles()
