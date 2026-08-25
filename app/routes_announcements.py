# -*- coding: utf-8 -*-
"""公告 API(T09):發布、有效公告、逐人已讀。

用途: 讓 `announcer` 發布一則對多人的公告;讓任何 `reader` 看到目前有效的
      公告,並標記**自己**讀過。
副作用: 發布會 INSERT 一列 `announcement`;標已讀會 INSERT 一列
      `announcement_read`(冪等)。

🔴 **三條紅線在本模組的落點:**

1. **身分不信前端** —— `author_sub` 一律自 token 取得。
   請求模型裡**根本沒有** `author_sub` 這個欄位(Pydantic 預設 `extra="ignore"`),
   所以不是「收下來再忽略」,是**沒有任何一條路徑讀得到它**。
   ⚠ 刻意不用 `extra="forbid"`:那會讓 API 反過來告訴呼叫方
   「這個欄位名存在」,等於幫人列出可以試的欄位。

2. **零人名** —— 回應**不含 `author_sub`、不含任何姓名**(契約 §4.2a L1;
   DI-3「同儕可見的顯示名稱」portal 尚未裁決)。與 T08 `_serialize` 同一條。
   公告的作者是一個**人**,而訊息階段一全是系統發的 —— 也就是說,
   公告才是這條紅線第一次真的有人名可以洩漏的地方。

3. **內容不進 log** —— log 只記公告 id、`sub`、事件類型。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.authz import (
    CAP_PUBLISH_ANNOUNCEMENT,
    CAP_READ_OWN,
    require_capability,
)
from app.db import session_scope
from app.models import AUDIENCE_ALL, AUDIENCES
from app.oidc import log_event
from app.repo import (
    create_announcement,
    list_active_announcements,
    mark_announcement_read,
)
from app.validation import (
    BadRequest,
    NotFound,
    iso_utc,
    parse_aware_datetime,
    require_choice,
    require_text,
)

# 對應 `Announcement.title` 的欄寬。🔴 超長必須在這裡擋掉:
# PostgreSQL 會報錯(500),而 SQLite 直接無視 —— 也就是本機測試綠、上線 500。
TITLE_MAX = 255


class PublishRequest(BaseModel):
    """發布公告的請求。

    🔴 **沒有 `author_sub` 欄位**,見模組檔頭第 1 條。
    ⚠ 時間一律是**帶時區的 ISO 8601 字串**(例 `2026-08-30T02:00:00+08:00`),
      不是 datetime 型別 —— 交給 Pydantic 解析的話,不帶時區的值會被
      安靜地收成 naive datetime,而那正是我方要擋的東西
      (見 `app/validation.py::parse_aware_datetime`)。
    """

    model_config = ConfigDict(extra="ignore")

    title: str = ""
    body: str = ""
    audience: str = AUDIENCE_ALL
    starts_at: str | None = None
    ends_at: str | None = None


def serialize_announcement(ann, is_read: bool) -> dict:
    """把一則公告轉成回應/模板用的 dict。

    參數: ann — Announcement;is_read — **這個人**讀過沒
    回傳: dict
    副作用: 無

    🔴 **刻意不輸出 `author_sub`,也不輸出任何姓名。**
       契約 §4.2a L1 禁止顯示名稱出現在一般使用者可見頁面,而 DI-3 未裁決。
       連 `author_sub` 都不給的理由與 T08 的 `sender_sub` 相同:
       欄位一旦存在,前端就會拿它去查姓名,那就繞過了裁決。
    """
    return {
        "id": str(ann.id),
        "title": ann.title,
        "body": ann.body,
        # 🔴 一律經 `iso_utc` 輸出:我方拒收不帶時區的**輸入**,
        #    那就不能吐出不帶時區的**輸出**(見 `app/validation.py::iso_utc`)。
        "starts_at": iso_utc(ann.starts_at),
        "ends_at": iso_utc(ann.ends_at),
        "is_read": is_read,
        "created_at": iso_utc(ann.created_at),
    }


def build_announcements_router() -> APIRouter:
    """建立公告路由。

    回傳: APIRouter(呼叫方負責加 `/inbox` 前綴)
    副作用: 無(只組 router)
    """
    router = APIRouter(prefix="/api/v1", tags=["announcements"])

    @router.post("/announcements", status_code=201)
    def publish(
        payload: PublishRequest,
        identity=Depends(require_capability(CAP_PUBLISH_ANNOUNCEMENT)),
    ):
        """發布一則公告。

        能力: `publish_announcement`(**`reader` 沒有** —— DEC-16 條件 C1 的邊界)
        回傳: 201 `{"id": ...}`
        錯誤: 無此能力 → 403;值不合法 → 400
        副作用: INSERT 一列 `announcement`

        🔴 **所有驗證都在 INSERT 之前**。順序反過來(先寫再驗)的話,
           400 的回應旁邊已經留下一列公告,而下一個查詢就會把它端出來 ——
           而測試若只斷言狀態碼,那個實作是綠的
           (`test_reader_cannot_publish_and_writes_nothing` 因此要數列數)。

        🔴 `ends_at <= starts_at` 一律 400。不擋的話發布會回 201、資料也進去了,
           而那則公告**永遠不會出現** —— 發布者沒有任何線索知道
           自己按的按鈕沒有作用。
        """
        sub, _roles = identity
        title = require_text(payload.title, field="title", max_length=TITLE_MAX)
        body = require_text(payload.body, field="body", max_length=20000)
        audience = require_choice(payload.audience, field="audience", allowed=AUDIENCES)

        starts_at = (
            parse_aware_datetime(payload.starts_at, field="starts_at")
            if payload.starts_at
            else None
        )
        ends_at = (
            parse_aware_datetime(payload.ends_at, field="ends_at")
            if payload.ends_at
            else None
        )
        if starts_at is None:
            # 沒給就是「現在起生效」。用資料庫預設會讓 starts_at 與比對用的
            # 「現在」來自兩個不同的時鐘,而差幾毫秒的症狀是**剛發布的公告
            # 第一次重新整理看不到**。
            from datetime import datetime, timezone

            starts_at = datetime.now(timezone.utc)
        if ends_at is not None and ends_at <= starts_at:
            raise BadRequest("invalid_window", "ends_at 必須晚於 starts_at")

        with session_scope() as session:
            row = create_announcement(
                session,
                author_sub=sub,          # 🔴 來自 token,不是 payload
                title=title,
                body=body,
                starts_at=starts_at,
                ends_at=ends_at,
                audience=audience,
            )
            announcement_id = str(row.id)
        # log 只記 id、sub、事件類型 —— 🔴 標題與內容**不進 log**(本專案紅線)
        log_event("announcement_published", sub=sub, announcement_id=announcement_id)
        return {"id": announcement_id}

    @router.get("/announcements/active")
    def active(identity=Depends(require_capability(CAP_READ_OWN))):
        """目前有效的公告,附**我**讀過沒。

        能力: `read_own`(核可範圍明列「看有效公告」)
        回傳: `{"items": [...], "unread": N}`
        副作用: 無(只讀)

        ⚠ `unread` 由同一次查詢的結果算出,**不另發一次 count 查詢**:
          兩個查詢會在兩個時間點跑,而清單與數字對不起來的畫面
          (「1 未讀」但清單裡每一則都已讀)看起來像資料庫壞掉。
        """
        sub, _roles = identity
        with session_scope() as session:
            rows = list_active_announcements(session, user_sub=sub)
            items = [serialize_announcement(a, read) for a, read in rows]
        return {"items": items, "unread": sum(1 for i in items if not i["is_read"])}

    @router.post("/announcements/{announcement_id}/read")
    def mark_read(
        announcement_id: str, identity=Depends(require_capability(CAP_READ_OWN))
    ):
        """標記**自己**讀過某則公告(冪等)。

        能力: `read_own`
        回傳: `{"id": ..., "read_at": ...}`
        錯誤: 公告不存在 → **404**
        副作用: 首次呼叫 INSERT 一列 `announcement_read`;第二次什麼都不做

        ⚠ 為什麼是 404,而訊息的同一種情況是 403:
          差別在**有沒有歸屬要保護**。訊息有(「這是不是你的」),
          回 404 會洩漏「這個 id 存在」;公告對所有人都可見,沒有歸屬,
          此時 403 反而會讓人以為是自己權限不足,查錯方向。

        🔴 寫入的 `user_sub` 一律是 token 的 `sub`,路徑與 body 都不參與。
        """
        sub, _roles = identity
        try:
            aid = uuid.UUID(announcement_id)
        except ValueError:
            raise NotFound("公告不存在")
        with session_scope() as session:
            row = mark_announcement_read(session, announcement_id=aid, user_sub=sub)
            if row is None:
                raise NotFound("公告不存在")
            read_at = iso_utc(row.read_at)
        log_event("announcement_read", sub=sub, announcement_id=str(aid))
        return {"id": str(aid), "read_at": read_at}

    return router
