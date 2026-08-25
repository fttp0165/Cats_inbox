# -*- coding: utf-8 -*-
"""收件匣讀取 API 與收件匣頁(T08)。

用途: 讓登入的人看到**自己的**訊息;未讀鈴鐺的計數端點;標已讀。
副作用: 讀資料庫;「標已讀」會 UPDATE 兩欄(冪等)。

🔴 本模組的每一個端點都只用**來自 token 的 `sub`** 當收件人,
   不接受任何來自 request 的身分欄位(本專案紅線)。
   實作上的落點:`app/repo.py` 的三個函式都把 `recipient_sub` 設為
   **必填參數且沒有「查全部」的分支** —— 那個分支一旦存在,
   少寫一個參數的呼叫端就會把全公司的通知端出去,
   而**畫面看起來完全正常**(只是訊息比較多)。

🔴 階段一**零人名**:列表與頁面一律不輸出 `display_name`。
   契約 §4.2a L1 明文「不得出現在一般使用者可見頁面」,
   而 DI-3(同儕可見的顯示名稱)portal **尚未裁決**。
   寄件人一律以 `source_app` 標籤呈現。
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.authz import (
    CAP_PUBLISH_ANNOUNCEMENT,
    CAP_READ_OWN,
    Forbidden,
    has_capability,
    require_capability,
)
from app.db import session_scope
from app.oidc import log_event
from app.repo import (
    count_unread,
    list_active_announcements,
    list_messages,
    mark_message_read,
)
from app.routes_announcements import serialize_announcement
from app.validation import iso_utc


def _safe_action_url(msg) -> str | None:
    """輸出側的 `action_url` 白名單(T10)。不合白名單就丟掉,並記一行 log。

    參數: msg — Message
    回傳: 合法的 action_url,或 None
    副作用: 丟掉時寫一行 log(**只記 id 與事件類型**,不記主旨/內容)

    🔴 **為什麼寫入側已經驗過還要再驗一次:**
       寫入側的白名單是 T10 才加的。在它之前寫進去的列、日後手動改過的列、
       被入侵寫進去的列**都繞過了它** —— 輸出側是唯一能保護**既有資料**的地方。
       站內通知天生長得「可信」,而 `javascript:` 連結在畫面上與正常連結
       **一模一樣**。
    ⚠ 丟掉時一定要留 log:靜默丟掉會讓「那顆按鈕不見了」查不出原因。
    """
    from app.validation import BadRequest, validate_action_url

    try:
        return validate_action_url(msg.action_url)
    except BadRequest as exc:
        log_event("action_url_rejected_on_output", message_id=str(msg.id), reason=exc.code)
        return None


def _serialize(msg) -> dict:
    """把 Message 轉成回應用的 dict。

    回傳: dict
    副作用: 無

    🔴 **刻意不輸出 `sender_sub`,也不輸出任何姓名。**
      - 姓名:契約 §4.2a L1 禁止出現在一般使用者可見頁面(DI-3 未裁決);
      - `sender_sub`:階段一全是系統通知(`sender_sub` 恆為 NULL),
        而把它放進回應等於預告「階段二會在這裡出現一個人的 id」——
        到時候前端拿它去查姓名,就繞過了 DI-3 的裁決。
        階段二要露出寄件人時,再依當時的裁決決定露出什麼。
    """
    return {
        "id": str(msg.id),
        "category": msg.category,
        "subject": msg.subject,
        "body": msg.body,
        # 🔴 **輸出側再驗一次**(T10)。寫入側的白名單是 T10 才加的,而在它之前
        #    寫進去的列、手動改過的列、被入侵寫進去的列都繞過了它 ——
        #    輸出側再擋一次是唯一能保護**既有資料**的做法。
        #    ⚠ 丟掉時記一行 log(只記 id):靜默丟掉會讓「按鈕不見了」查不出原因。
        "action_url": _safe_action_url(msg),
        "source_app": msg.source_app,
        "is_read": msg.is_read,
        # 🔴 T09 起改走 `iso_utc`:原本的 `.isoformat()` 在 **SQLite** 上
        #    吐出的是**不帶時區**的字串(它不存時區,PostgreSQL 的
        #    `timestamptz` 會保留)——同一個欄位在「剛寫入」與「重讀」時
        #    長得不一樣,而我方的寫入端正是拒收不帶時區的值。
        #    輸出與輸入必須對稱,見 `app/validation.py::iso_utc`。
        "read_at": iso_utc(msg.read_at),
        "created_at": iso_utc(msg.created_at),
    }


def build_inbox_router(*, settings) -> APIRouter:
    """建立讀取 API 與收件匣頁的路由。

    參數: settings — Settings 快照
    回傳: APIRouter(呼叫方負責加 `/inbox` 前綴)
    副作用: 無(只組 router)
    """
    router = APIRouter(tags=["inbox"])
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    api = APIRouter(prefix="/api/v1")

    @api.get("/messages")
    def list_own_messages(
        unread: bool = False,
        identity=Depends(require_capability(CAP_READ_OWN)),
    ):
        """列出**自己的**訊息。

        參數: unread — `true` 時只回未讀
        回傳: {"items": [...]}
        副作用: 無

        ⚠ 函式簽章裡**沒有**任何收件人參數,所以 FastAPI 不會把
        `?recipient_sub=...` 綁進來——前端傳了也只是被忽略。
        這比「收下來再檢查」少一條出錯的路徑。
        """
        sub, _roles = identity
        with session_scope() as session:
            rows = list_messages(session, recipient_sub=sub, unread_only=unread)
            return {"items": [_serialize(m) for m in rows]}

    @api.get("/messages/unread-count")
    def unread_count(identity=Depends(require_capability(CAP_READ_OWN))):
        """未讀數(入口鈴鐺每 30 秒打這裡)。

        回傳: {"unread": N}
        副作用: 無

        ⚠ 這個端點的成本要一直很低:它乘上「線上人數 × 每分鐘兩次」。
        走 `count(*)` + `ix_message_recipient_unread` 索引。
        """
        sub, _roles = identity
        with session_scope() as session:
            return {"unread": count_unread(session, recipient_sub=sub)}

    @api.post("/messages/{message_id}/read")
    def mark_read(message_id: str, identity=Depends(require_capability(CAP_READ_OWN))):
        """把自己的某一則訊息標為已讀(**冪等**)。

        回傳: {"id": ..., "is_read": true, "read_at": ...}
        錯誤: 不是自己的訊息(或不存在)→ **403**
        副作用: 首次呼叫 UPDATE `is_read` / `read_at`;第二次**不改變 `read_at`**

        ⚠ 為什麼別人的訊息回 403 而不是 404:訊息 id 是 **UUIDv4,猜不到**——
        能打到這裡表示你已經有那個 id 了,403 沒有多洩漏什麼;
        而 401/403/404 三者混用會讓查問題的人分不出
        「沒登入 / 沒權限 / 不存在」。
        🔴 **若日後 id 改成可猜的形式(序號),這個決定必須改回 404。**
        """
        sub, _roles = identity
        try:
            mid = uuid.UUID(message_id)
        except ValueError:
            # 形狀不對的 id 與「別人的 id」一律同一個答案,不多說什麼
            raise Forbidden(CAP_READ_OWN)
        with session_scope() as session:
            row = mark_message_read(session, message_id=mid, recipient_sub=sub)
            if row is None:
                raise Forbidden(CAP_READ_OWN)
            # log 只記 id 與事件類型 —— 🔴 主旨與內容**不進 log**(本專案紅線)
            log_event("message_read", sub=sub, message_id=str(mid))
            return {"id": str(row.id), "is_read": row.is_read,
                    "read_at": row.read_at.isoformat() if row.read_at else None}

    router.include_router(api)

    @router.get("/", include_in_schema=False, response_class=HTMLResponse)
    def inbox_page(request: Request, identity=Depends(require_capability(CAP_READ_OWN))):
        """收件匣頁(伺服器端算繪)。

        回傳: 200 HTML
        副作用: 無(只讀)

        🔴 **階段一零人名**:模板拿到的每一筆都只有 `source_app`,
           連 `sender_sub` 都沒有(見 `_serialize` 的說明);
           公告那一區同樣不帶 `author_sub`(見 `serialize_announcement`)。
        ⚠ 未登入時這一頁是 **401**,不是導向 IdP —— 導向要等 T12 的入口整合
           決定「深層頁未登入該怎麼辦」(契約對 upload-program 的核准偏離
           只涵蓋**首頁本身**,不涵蓋深層頁)。

        🔴 **T09 起這一頁同時顯示公告。** 沒有這一段的話,公告上線之後
           沒有任何畫面顯示它 —— 而發布 API 回 201、`active` 也回得出來,
           **零錯誤訊息的功能缺口**(理由與處置見 T09 dev-log 計畫段)。
        """
        sub, roles = identity
        # 🔴 只在**真的有能力**時才顯示發布入口(T09b)。
        #    顯示一個點下去會 403 的連結**比不顯示更糟** —— 它讓人以為
        #    自己做錯了什麼,而其實他從來沒有那個權限。
        can_publish = has_capability(roles, CAP_PUBLISH_ANNOUNCEMENT)
        with session_scope() as session:
            rows = list_messages(session, recipient_sub=sub)
            items = [_serialize(m) for m in rows]
            unread = count_unread(session, recipient_sub=sub)
            announcements = [
                serialize_announcement(a, read)
                for a, read in list_active_announcements(session, user_sub=sub)
            ]
        return templates.TemplateResponse(
            request=request,
            name="inbox.html",
            context={
                "items": items,
                "unread": unread,
                "announcements": announcements,
                "announcements_unread": sum(
                    1 for a in announcements if not a["is_read"]
                ),
                "can_publish": can_publish,
                "base_path": settings.base_path,
                # 🔴 刻意傳 `sub` 而不是姓名:待開通頁已經是這個做法(契約 §4.3),
                #    而收件匣頁**不顯示任何人名**(§4.2a L1、DI-3 未裁決)。
                "sub": sub,
            },
            headers={"Cache-Control": "no-store"},
        )

    return router
