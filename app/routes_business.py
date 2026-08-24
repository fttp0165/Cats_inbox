# -*- coding: utf-8 -*-
"""業務端點的**授權外殼**(T05)。

用途: 讓 T05 的範圍(核可條件 C1)有實際的端點可以被反向測試打。
副作用: 無——本階段不讀寫任何業務資料。

🔴 為什麼 T05 就要有這些端點,而不是等 T08/T09 一起做:
   核可條件 **C3** 要求「以反向測試釘住範圍並納入 CI」。
   反向測試需要一個**實際存在的端點**可以打——沒有端點的話,
   「`reader` 不能寄信」這句話在測試層是無法表達的,
   而等到 T08 寫寄信功能時,授權是那次的**附帶工作**,
   附帶工作是最容易被忘記的那一種。

🔴 已授權時一律回 **501**,不回 200 空清單。
   回 200 空清單會說「你沒有訊息」——那是一句**假話**,因為表還不存在。
   501 說的是「你有權限,但這個功能還沒做」,那是真的。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.authz import (
    CAP_PUBLISH_ANNOUNCEMENT,
    CAP_SEND_MESSAGE,
    require_capability,
)

_NOT_YET = JSONResponse(
    {"error": "not_implemented", "detail": "授權已通過;功能實作在 T08/T09"},
    status_code=501,
)


def build_business_router() -> APIRouter:
    """建立業務端點的授權外殼。

    回傳: APIRouter(呼叫方負責加 `/inbox` 前綴)
    副作用: 無
    """
    router = APIRouter(prefix="/api/v1", tags=["messages"])

    # ⚠ `GET /messages` 的 501 外殼已於 **T08** 移除 —— 它現在由
    #   `app/routes_inbox.py` 真正實作(含 `unread-count` 與標已讀)。
    #   留著一個回 501 的同路徑端點會遮蔽真正的實作,而 FastAPI 不會警告
    #   ——先註冊的那個贏,於是「功能做完了卻還是 501」。

    @router.post("/messages")
    def send_message(_=Depends(require_capability(CAP_SEND_MESSAGE))):
        """寄站內信。

        能力:`send_message`(**`reader` 沒有** —— 這就是 C1 的邊界)
        🔴 T09 實作時,`sender_sub` 一律由後端自 token 取得(本專案紅線)。
        """
        return _NOT_YET

    @router.post("/announcements")
    def publish_announcement(_=Depends(require_capability(CAP_PUBLISH_ANNOUNCEMENT))):
        """發布公告。

        能力:`publish_announcement`(**`reader` 沒有**)
        """
        return _NOT_YET

    return router
