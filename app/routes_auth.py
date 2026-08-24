# -*- coding: utf-8 -*-
"""OIDC 登入路由(T04):`/oidc/login`、`/oidc/callback/`、`/me`。

用途: 把 Authorization Code + PKCE 的兩段流程接起來,並在每一次請求上
      維護 session(含契約 §3.3 要求的伺服器端主動續期)。
副作用: 設定/刪除 cookie、對 IdP 發請求、在記憶體建立 session。

🔴 這支模組刻意**不做授權判定**。首登建號、自動授 `reader`、待開通頁
   一律是 T05 的事。理由:登入(你是誰)與授權(你能做什麼)混在一起,
   之後要單獨停用 `reader`(portal 核可條件 C2)就沒有下手的地方。
"""

from __future__ import annotations

import secrets

from pathlib import Path

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.oidc import (
    OidcClient,
    OidcError,
    code_challenge_for,
    log_event,
    new_code_verifier,
)
from app.session import (
    REFRESH_MARGIN_SECONDS,
    SESSION_COOKIE,
    TX_COOKIE,
    TX_TTL_SECONDS,
    PendingLogin,
    SessionData,
    SessionStore,
)


def build_auth_router(*, settings, oidc: OidcClient, store: SessionStore, clock) -> APIRouter:
    """建立 auth 路由。

    參數:
      settings — Settings 快照
      oidc     — OidcClient(已注入 transport 與 clock)
      store    — SessionStore
      clock    — 回傳 epoch 秒的可呼叫物
    回傳: APIRouter(呼叫方負責加 `/inbox` 前綴)
    副作用: 無(只組 router)
    """
    router = APIRouter(tags=["auth"])
    cookie_kwargs = store.cookie_kwargs(settings.base_path)
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

    # 🔴 登出後的落地頁,必須是 client 已登記的 post-logout 值之一,**逐字**。
    #    登記的兩個是 `/inbox/` 與 `/inbox/logged-out/`;選後者的理由見
    #    `logged_out()` 的 docstring。未登記的值會讓 Keycloak 拒絕導回,
    #    使用者停在 IdP 頁面上,而我方 log 只看到「他登出了」——一切正常。
    post_logout_redirect = f"{settings.public_base_url.rstrip('/')}{settings.base_path}/logged-out/"

    def _current(request: Request):
        """取當前 session,必要時主動續期。

        回傳: (session_key, SessionData)
        錯誤: 未登入或續期失敗 → OidcError(401)
        副作用: 可能對 IdP 發 refresh 請求並更新 session

        🔴 續期就寫在這裡,不是寫成「定時任務」:伺服器端算繪的 App 只有在
        使用者實際發請求時才有機會續期,而那正是需要它有效的時刻。
        契約 §3.3:不做續期的症狀是**登入 5 分鐘後靜默退回未登入,伺服器零錯誤**。
        """
        key = store.unseal(request.cookies.get(SESSION_COOKIE))
        data = store.get(key)
        if data is None:
            raise OidcError(401, "not_authenticated", "無有效 session")

        if data.access_expires_at - clock() > REFRESH_MARGIN_SECONDS:
            return key, data

        if not data.refresh_token:
            # 沒有 refresh_token 可用:過期就是過期,不得沿用
            store.delete(key)
            raise OidcError(401, "session_expired", "access token 已過期且無 refresh_token")

        try:
            tokens = oidc.refresh(data.refresh_token)
        except OidcError as exc:
            # 🔴 refresh 失敗 = IdP 那邊 session 沒了(帳號停用、逾時、被登出)。
            # 這一刻必須登出;沿用舊 session 會讓契約 §3.3 換來的
            # 「收權即時性」變成假的(帳號停用後還能繼續用)。
            store.delete(key)
            log_event("oidc_refresh_failed", sub=data.sub, error=exc.code)
            raise OidcError(401, "session_expired", "refresh 失敗,已登出")

        refreshed = _session_from_tokens(tokens, previous=data)
        store.replace(key, refreshed)
        log_event("oidc_refreshed", sub=refreshed.sub)
        return key, refreshed

    def _session_from_tokens(tokens: dict, *, previous: SessionData | None = None,
                             nonce: str | None = None) -> SessionData:
        """把 token response 轉成 SessionData(含 id_token 驗證)。

        參數:
          tokens   — token 端點回應
          previous — 續期時的舊 session(用來延續 sub/sid 並容忍缺項)
          nonce    — 首次登入時比對用;續期不傳(規範上 refresh 換來的
                     id_token 不帶 nonce,硬要比會讓續期永遠失敗)
        回傳: SessionData
        錯誤: id_token 驗不過 → OidcError(401)
        """
        access = tokens.get("access_token", "")
        id_token = tokens.get("id_token", "")
        claims = oidc.verify_id_token(id_token, nonce=nonce, access_token=access or None)

        expires_in = float(tokens.get("expires_in", 300))
        return SessionData(
            sub=claims["sub"],
            idp_sid=claims.get("sid") or (previous.idp_sid if previous else None),
            access_token=access,
            # Keycloak 續期會給新的 refresh_token;沒給就沿用舊的
            refresh_token=tokens.get("refresh_token")
            or (previous.refresh_token if previous else None),
            id_token=id_token,
            access_expires_at=clock() + expires_in,
            created_at=previous.created_at if previous else clock(),
        )

    # ── 登入 ──────────────────────────────────────────────────────
    @router.get("/oidc/login", include_in_schema=False)
    def login(request: Request):
        """導向 Keycloak 授權端點(Authorization Code + PKCE S256)。

        回傳: 302
        副作用: 設 `inbox_oidc_tx` cookie(存 state/nonce/verifier 的索引)
        """
        verifier = new_code_verifier()
        pending = PendingLogin(
            state=secrets.token_urlsafe(24),
            nonce=secrets.token_urlsafe(24),
            code_verifier=verifier,
            created_at=clock(),
        )
        tx_key = store.start_login(pending)
        url = oidc.authorization_url(
            state=pending.state, nonce=pending.nonce, code_challenge=code_challenge_for(verifier)
        )

        # 🔴 契約 v2.14 的落點:把**實際要送出的** redirect_uri 印出來,
        # 供人眼與 client 登記值逐字比對。PLM 的事故是實際送出值與登記值不同,
        # 而錯誤停在 Keycloak 的頁面、自家 log 是空的——這一行就是那個空的 log。
        log_event(
            "oidc_login_redirect",
            redirect_uri=oidc.redirect_uri,
            code_challenge_method="S256",
            scope="openid",
        )

        resp = RedirectResponse(url, status_code=302)
        resp.set_cookie(TX_COOKIE, store.seal(tx_key), max_age=TX_TTL_SECONDS, **cookie_kwargs)
        return resp

    # ── callback ──────────────────────────────────────────────────
    # 🔴 路徑**帶結尾斜線**,逐字對齊 client 登記值
    # `https://catsapp.sporton.com.tw/inbox/oidc/callback/`。
    # OAuth 對 redirect_uri 是逐字比對:少一個斜線就是另一個值。
    @router.get("/oidc/callback/", include_in_schema=False)
    def callback(request: Request, code: str | None = None, state: str | None = None,
                 error: str | None = None):
        """接 IdP 的授權碼,換 token、驗 id_token、建 session。

        回傳: 302 到 `{base_path}/me`(T08 會改成收件匣首頁)
        副作用: 建立 session、設 session cookie、刪交易 cookie
        """
        if error:
            # IdP 明確回錯(使用者按取消、consent 被拒)——不是我方的錯,別當 500
            log_event("oidc_callback_idp_error", error=error)
            return JSONResponse({"error": error}, status_code=400)

        pending = store.take_login(store.unseal(request.cookies.get(TX_COOKIE)))
        if pending is None:
            # 交易不存在:cookie 過期、被清、或**同一個 callback 被重放第二次**
            return JSONResponse({"error": "no_login_transaction"}, status_code=400)
        if not state or not secrets.compare_digest(state, pending.state):
            # state 是 CSRF 防護;不符一律拒,且**不建 session**
            log_event("oidc_state_mismatch")
            return JSONResponse({"error": "state_mismatch"}, status_code=400)
        if not code:
            return JSONResponse({"error": "missing_code"}, status_code=400)

        try:
            tokens = oidc.exchange_code(code=code, code_verifier=pending.code_verifier)
            data = _session_from_tokens(tokens, nonce=pending.nonce)
        except OidcError as exc:
            # 💡 契約 v2.17:壞 code 在這裡會得到 `invalid_grant`,
            # 而 secret 錯會是 `invalid_client`——log 這個代碼就能分辨兩者。
            log_event("oidc_callback_failed", error=exc.code)
            return JSONResponse({"error": exc.code}, status_code=exc.status_code)

        key = store.create(data)
        log_event("oidc_login_ok", sub=data.sub)

        resp = RedirectResponse(f"{settings.base_path}/me", status_code=302)
        resp.set_cookie(SESSION_COOKIE, store.seal(key), **cookie_kwargs)
        resp.delete_cookie(TX_COOKIE, path=cookie_kwargs["path"])
        return resp

    # ── 自發登出(契約 §10,方向一)────────────────────────────────
    @router.get("/logout", include_in_schema=False)
    def logout(request: Request):
        """使用者主動登出:**先清本地 session**,再導 IdP 的 end_session。

        回傳: 302(有 session → IdP;無 session → 已登出頁)
        副作用: 刪除本地 session 與 session cookie

        🔴 順序不能反。先導 IdP 的話,使用者中途關掉分頁、或 IdP 當下不可用,
        本地 session 就還活著——他按了登出,而 inbox 還認得他。
        本地清除是我方能保證的部分,IdP 那一段是 best-effort。
        """
        key = store.unseal(request.cookies.get(SESSION_COOKIE))
        data = store.get(key)
        store.delete(key)          # ← 先刪,再組導向

        if data is None:
            # 沒有 session 就沒有 id_token_hint,拿去 end_session 只會得到
            # 一個要使用者確認的頁面。直接送他到已登出頁,語意正確也少一次往返。
            target = f"{settings.base_path}/logged-out/"
        else:
            target = oidc.end_session_url(
                id_token_hint=data.id_token,
                post_logout_redirect=post_logout_redirect,
            )
            log_event("oidc_logout", sub=data.sub)

        resp = RedirectResponse(target, status_code=302)
        resp.delete_cookie(SESSION_COOKIE, path=cookie_kwargs["path"])
        resp.headers["Cache-Control"] = "no-store"
        return resp

    # ── 被通知登出(契約 §10.3,方向二)────────────────────────────
    # 🔴 路徑**無結尾斜線**,逐字對齊 client 的 `frontchannel.logout.url`
    #    登記值 `https://catsapp.sporton.com.tw/inbox/oidc/frontchannel-logout`。
    #    §10.3a:真正的規格不是「無斜線」,是**與登記值逐字相同**——
    #    Keycloak 拿它去**呼叫**而非比對,差一個字元的症狀是**端點零呼叫**,
    #    而那與「設定根本沒套用」完全無法區分(PLM 2026-08-14 前例)。
    @router.get("/oidc/frontchannel-logout", include_in_schema=False, status_code=204)
    def frontchannel_logout(request: Request, sid: str | None = None, iss: str | None = None):
        """IdP 通知登出(front-channel):清 session、回 204。

        參數: sid / iss — Keycloak 會帶(portal 註冊時開 `session.required=true`);
              **帶與不帶都必須能處理**
        回傳: 204(永遠;冪等)
        副作用: 刪除 session 與 session cookie —— **只有刪,沒有別的**

        🔴 本端點**免認證**:契約 §10.3——IdP 是以 iframe 載入它,
        那個請求不會帶任何 API token。免認證的代價由「只准刪」來封住:
        「**被任意第三方呼叫的最壞後果必須是使用者被登出**」。
        故此處不得建立 session、不得寫業務資料、不得依 sid 反查任何個資。

        🔴 兩條刪除路徑都要有,少一條就會靜默漏掉:
          ① 依 `sid` 刪 —— IdP 的 iframe **可能沒有那個人的 cookie**
             (分頁凍結、cookie 政策、或從別的脈絡發起)。只靠 cookie 的實作
             在那種情況下靜默不生效:使用者以為登出了,inbox 還是登入狀態。
             這也是 T04 決定「session 放伺服器端」的唯一理由。
          ② 依本次請求的 cookie 刪 —— 同站 iframe 通常帶得到 cookie,
             而**無 `sid` 的呼叫只有這條路**。
        """
        removed = 0
        if sid:
            removed += store.delete_by_idp_sid(sid)
        key = store.unseal(request.cookies.get(SESSION_COOKIE))
        if key:
            store.delete(key)
            removed += 1
        # 只記事件與數量,不記 sid 內容也不記 sub(§10.3 的最小副作用原則)
        log_event("oidc_frontchannel_logout", removed=removed, had_sid=bool(sid))

        resp = Response(status_code=204)
        resp.delete_cookie(SESSION_COOKIE, path=cookie_kwargs["path"])
        # 🔴 被快取的登出等於登出無效,而症狀是「有時登得出、有時登不出」
        #    ——間歇性症狀沒有人查得動。
        resp.headers["Cache-Control"] = "no-store"
        return resp

    # ── 已登出頁 ──────────────────────────────────────────────────
    @router.get("/logged-out/", include_in_schema=False, response_class=HTMLResponse)
    def logged_out(request: Request):
        """已登出說明頁(登出後的落地頁)。

        回傳: 200 HTML
        副作用: 無

        🔴 為什麼需要這一頁,而不是像原本規劃那樣導回 `/inbox/`:
        導回 `/inbox/` 會讓未登入者立刻被推去 IdP 登入頁,使用者**看不出
        登出成功沒有**;而萬一 SSO session 還在,他會被**靜默登回去**
        ——那正是契約 §10 緣起描述的「登出看起來沒用」。
        本路徑 `/inbox/logged-out/` **早已在 client 的 post-logout 登記值裡**,
        所以改用它不需要 portal 動任何設定。
        """
        return templates.TemplateResponse(
            request=request,
            name="logged_out.html",
            context={"login_url": f"{settings.base_path}/oidc/login"},
            headers={"Cache-Control": "no-store"},
        )

    # ── 身分探針 ──────────────────────────────────────────────────
    @router.get("/me")
    def me(session=Depends(_current)):
        """回報「我是誰」。

        回傳: 200 {"authenticated": true, "sub": ...} / 401
        副作用: 可能觸發續期(見 `_current`)

        用途有兩個,都刻意:
          ① 這是 T04 唯一能讓人**親眼看到登入成功**的頁面(第九條7);
          ② 續期是否真的有作用,只能靠「過了 300 秒還回 200」來證明。
        ⚠ T08 會把收件匣首頁接上來,callback 的導向目標屆時改成首頁。
        🔴 只回 `sub`,不回姓名/email——本服務不取那些 claim。
        """
        _key, data = session
        return {
            "authenticated": True,
            "sub": data.sub,
            "access_expires_in": int(data.access_expires_at - clock()),
        }

    return router
