# -*- coding: utf-8 -*-
"""cats-inbox FastAPI 應用進入點。

用途: 建立 app 實例、掛上 `/inbox` 前綴的路由。
副作用: 無(健康檢查不連 DB;OIDC 路由只在設定齊備時才註冊)。

🔴 為什麼路由前綴寫在應用裡,而不是靠 gateway 去掉前綴:
   本服務掛在 `catsapp.sporton.com.tw/inbox/`(D2″ 單一 hostname 之下),
   契約 §4.10 要求「前端 base path 必須設為你的子路徑」。
   把前綴放進應用,代表本機、容器、經 gateway 三種情境下的 URL **完全一致**;
   靠 gateway 改寫路徑的話,本機測全對、上線後靜態資源與 redirect 全歪,
   而那種錯只會在登入當下才發現(契約 §4.1 已記載同型的坑)。
"""

from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

from app import __version__
from app import clock as clock_module
from app.config import get_settings
from app.oidc import OidcClient, OidcError


def create_app(*, transport=None, clock=None) -> FastAPI:
    """建立 app 實例。

    參數:
      transport — OIDC 的 HTTP 傳輸(測試注入替身;正式環境留空用 httpx)
      clock     — 回傳 epoch 秒的可呼叫物(測試可推進;正式環境用真實時鐘)
    回傳: FastAPI
    副作用: 無(不連線、不建表)

    做成工廠而不是模組層的單一實例,理由有二:
      ① OIDC 路由**是否註冊**取決於環境變數(見下),而模組層實例會在
         第一次 import 時就把設定凍住,測試無法在同一個行程裡驗兩種狀態;
      ② 傳輸與時鐘要能被注入——契約 §3.3 的兩條紅線(±30s、300 秒續期)
         只在時間邊界上出錯,沒有可注入的時鐘就只能靠人手動等 5 分鐘。
    """
    settings = get_settings()
    now = clock or clock_module.now

    app = FastAPI(
        title="cats-inbox",
        version=__version__,
        # OpenAPI 文件也掛在子路徑下,避免與平台其他 App 的 /docs 撞路徑
        docs_url=f"{settings.base_path}/docs",
        openapi_url=f"{settings.base_path}/openapi.json",
    )

    # 所有路由統一掛前綴;新增路由一律加進這個 router,不要直接掛 app
    router = APIRouter(prefix=settings.base_path)

    @router.get("/health", tags=["ops"])
    def health() -> dict:
        """健康檢查。

        回傳: {"status": "ok", "version": <版本常數>}
        副作用: 無

        🔴 **刻意不查 DB**(平台容器紅線)。理由:健康檢查是 orchestrator 判斷
        「要不要重啟/摘掉這個容器」的依據。把 DB 查詢放進來,等於讓 DB 一慢
        就把原本健康的 API 容器連帶判死,故障範圍反而被放大。
        DB 的可用性由 `cats-inbox-pg` 自己的 healthcheck 負責。
        """
        return {"status": "ok", "version": __version__}

    # ── OIDC 路由:設定齊備才註冊 ──────────────────────────────────
    # 🔴 這就是 T04 的回滾閥門(比照 portal 對 PLM 要求的 `PLM_SSO_ENABLED`
    #    預設 off、off 時行為逐字不變)。issuer 或 session secret 沒設好時
    #    **不註冊**登入路由,而不是註冊一個會在使用者點下去才炸的路由——
    #    「還沒設定完」因此是一個明確狀態(404),不是一個 500。
    if settings.oidc_issuer and settings.session_secret:
        from app.routes_admin import build_admin_router
        from app.routes_announcements import build_announcements_router
        from app.routes_auth import build_auth_router
        from app.routes_business import build_business_router
        from app.routes_inbox import build_inbox_router
        from app.session import SessionStore

        # 🔴 建立 engine,但**不** create_all —— schema 的唯一權威是
        #    `alembic/versions/`(見 `app/db.py` 檔頭)。engine 是 lazy 的,
        #    所以 DB 不可用時建立本身不會失敗,健康檢查也仍然是 200。
        from app.db import init_engine

        init_engine(settings.db_url or "postgresql+psycopg://localhost/cats_inbox")

        oidc = OidcClient(settings, transport=transport, clock=now)
        store = SessionStore(settings.session_secret, clock=now)
        router.include_router(build_auth_router(
            settings=settings, oidc=oidc, store=store, clock=now
        ))
        router.include_router(build_inbox_router(settings=settings))
        router.include_router(build_announcements_router())
        router.include_router(build_business_router())
        router.include_router(build_admin_router(settings=settings))
        # `app.state` 是 `app/deps.py` 取用 store/oidc/clock 的唯一途徑
        # ——相依函式拿不到 closure,只拿得到 request。
        app.state.oidc = oidc
        app.state.session_store = store
        app.state.clock = now

    app.include_router(router)

    # ── 安全標頭 + 逐請求 CSP nonce(T10,契約 §4.10「嚴格 CSP」)──────
    @app.middleware("http")
    async def _security_headers(request, call_next):
        """對**每一個**回應加上安全標頭,並發一個**這一次專用**的 CSP nonce。

        副作用: 在 `request.state.csp_nonce` 留下 nonce 供模板取用

        🔴 **為什麼是 middleware,不是逐路由加標頭:**
           逐路由加的話,下一個新頁面會忘記,而**忘記不會有任何症狀**
           ——頁面照樣顯示,只是沒有 CSP 保護。
           守門是 `tests/test_security.py::test_every_html_response_has_csp`,
           它**列舉所有回 HTML 的路由**,所以新頁面自動被涵蓋。

        🔴 **nonce 每個請求都不同。** 寫死一個等於完全沒有 nonce
           (攻擊者注入一次就永久有效),而寫死的版本在畫面上與正確的版本
           **一模一樣**。

        🔴 **沒有 `script-src` 這一項是刻意的** —— 它會落回
           `default-src 'none'`,也就是**本服務不執行任何 JavaScript**。
           全站是伺服器端算繪,現在一支腳本都沒有。
           ⚠ 日後要加腳本,必須同時在此加 `script-src 'nonce-...'`
           並在那個 `<script>` 上帶 nonce;不加的話腳本會被靜默擋掉。

        ⚠ `frame-ancestors 'none'` 表示**不可被嵌入 iframe**。現行設計是
           入口頁「連結」到本服務而非嵌入(T12 只加鈴鐺);若日後改為嵌入,
           這裡必須改成允許入口來源,否則 iframe 會是一片空白**而零錯誤訊息**。
        """
        import secrets

        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; "
            f"style-src 'self' 'nonce-{nonce}'; "
            "img-src 'self' data:; "
            "font-src 'self'; "
            "connect-src 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'none'"
        )
        # 這兩個不屬於 CSP,但缺了同樣沒有症狀
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    # ── 靜態資源(T08)────────────────────────────────────────────────
    # 🔴 掛載點是 `{base_path}/assets`,對應目錄 `app/static/assets`。
    #    portal 2026-08-03 踩過:檔案放在 mount 目錄**之外**時,
    #    它在 repo 裡、路徑也算得對,而 app 根本不會送出去 ——
    #    瀏覽器拿到 404,**整頁沒有樣式而伺服器零錯誤**。
    #    守門:`tests/test_static.py::test_every_template_asset_is_actually_served`
    #    ——它打實際的 URL,不是檢查檔案在不在 repo 裡(後者正是當時通過的那種檢查)。
    _assets = Path(__file__).parent / "static" / "assets"
    app.mount(f"{settings.base_path}/assets", StaticFiles(directory=str(_assets)), name="assets")

    @app.exception_handler(OidcError)
    def _oidc_error_handler(_request, exc: OidcError):
        """把 OIDC 錯誤轉成對應狀態碼的 JSON。

        🔴 401 與 403 不得混用(契約 §11.5 的分界對登入同樣適用):
           本處只會產生 400/401/503;403「已認證但未開通」是 T05 的事。
           錯誤內容只給代碼,不給細節——細節只進 log,不告訴呼叫方
           「是哪一項不對」(那等於幫攻擊者縮小範圍)。
        """
        return JSONResponse({"error": exc.code}, status_code=exc.status_code)

    return app


# uvicorn 的進入點(`app.main:app`)
app = create_app()
