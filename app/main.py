# -*- coding: utf-8 -*-
"""cats-inbox FastAPI 應用進入點(T01 骨架:只有健康檢查)。

用途: 建立 app 實例、掛上 `/inbox` 前綴的路由。
副作用: 無(本階段不連 DB、不發外部請求)。

🔴 為什麼路由前綴寫在應用裡,而不是靠 gateway 去掉前綴:
   本服務掛在 `catsapp.sporton.com.tw/inbox/`(D2″ 單一 hostname 之下),
   契約 §4.10 要求「前端 base path 必須設為你的子路徑」。
   把前綴放進應用,代表本機、容器、經 gateway 三種情境下的 URL **完全一致**;
   靠 gateway 改寫路徑的話,本機測全對、上線後靜態資源與 redirect 全歪,
   而那種錯只會在登入當下才發現(契約 §4.1 已記載同型的坑)。
"""

from fastapi import APIRouter, FastAPI

from app import __version__
from app.config import get_settings

settings = get_settings()

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


app.include_router(router)
