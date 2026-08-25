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
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict

from app.authz import (
    CAP_PUBLISH_ANNOUNCEMENT,
    CAP_READ_OWN,
    require_capability,
)
from app.csrf import csrf_token_for, require_csrf
from app.db import session_scope
from app.models import AUDIENCE_ALL, AUDIENCES
from app.oidc import log_event
from app.deps import resolve_session
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

# 🔴 表單的 `datetime-local` **不帶時區**,而 `parse_aware_datetime` 拒收不帶時區的值。
#    這裡用**固定位移 +08:00** 把表單值補成有時區的值,而頁面上明寫「台北時間」。
#    ⚠ **這不是放寬 T09 的規則**:API 拒收是因為「呼叫方可能在任何時區,我方無從得知」;
#    表單可以補是因為「頁面已經告訴使用者這個欄位是什麼時區」—— 宣告不是猜。
#    ⚠ 用固定位移而非 `ZoneInfo("Asia/Taipei")`:台灣**沒有日光節約**,兩者恆等,
#    而固定位移不依賴 tzdata。🔴 換到有 DST 的時區時這個寫法會錯,屆時必須改成 ZoneInfo。
FORM_TZ = timezone(timedelta(hours=8))
FORM_TZ_LABEL = "台北時間(UTC+8)"


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


def _validate_and_create(
    *, author_sub: str, title: str, body: str, audience: str,
    starts_at_raw: str | None, ends_at_raw: str | None,
) -> str:
    """驗值 → 建立公告 → 回傳 id。**API 與表單共用這一條路。**

    參數: `*_raw` 為**帶時區**的 ISO 8601 字串(表單那邊已先補上位移)
    回傳: 新公告的 id(字串)
    副作用: INSERT 一列 `announcement`
    錯誤: 任何值不合法 → BadRequest(400),且**不寫入任何東西**

    🔴 **抽成一處而不是各寫一份。** 兩份各驗一半的話,兩邊遲早會漂移,
       而**漂移之後寬的那一邊贏** —— 攻擊者只會走那一邊。
    🔴 所有驗證都在 INSERT 之前。順序反過來的話,400 的回應旁邊
       已經留下一列公告,而下一個查詢就會把它端出來。
    """
    safe_title = require_text(title, field="title", max_length=TITLE_MAX)
    safe_body = require_text(body, field="body", max_length=20000)
    safe_audience = require_choice(audience, field="audience", allowed=AUDIENCES)

    starts_at = parse_aware_datetime(starts_at_raw, field="starts_at") if starts_at_raw else None
    ends_at = parse_aware_datetime(ends_at_raw, field="ends_at") if ends_at_raw else None
    if starts_at is None:
        # 沒給就是「現在起生效」。用資料庫預設會讓 starts_at 與比對用的
        # 「現在」來自兩個不同的時鐘,而差幾毫秒的症狀是**剛發布的公告
        # 第一次重新整理看不到**。
        starts_at = datetime.now(timezone.utc)
    if ends_at is not None and ends_at <= starts_at:
        raise BadRequest("invalid_window", "失效時間必須晚於生效時間")

    with session_scope() as session:
        row = create_announcement(
            session,
            author_sub=author_sub,      # 🔴 來自 token,不是 payload / 表單
            title=safe_title,
            body=safe_body,
            starts_at=starts_at,
            ends_at=ends_at,
            audience=safe_audience,
        )
        return str(row.id)


def _form_time_to_iso(value: str | None) -> str | None:
    """把 `datetime-local` 的值(不帶時區)補上表單宣告的位移。

    參數: value — 例 `2026-08-30T02:00`(可為 None / 空字串)
    回傳: 例 `2026-08-30T02:00+08:00`;沒給則 None
    副作用: 無

    🔴 **這裡是「補上宣告的時區」,不是「猜時區」。**
       頁面上明寫這兩個欄位是台北時間(見 `announcement_new.html`),
       所以補位移是把使用者已知的事寫成機器讀得懂的形式。
       ⚠ 對照 `parse_aware_datetime`:它拒收不帶時區的輸入,因為**API 的呼叫方
       可能在任何時區,我方無從得知**。兩者不衝突 —— 差別在「有沒有告知」。
    ⚠ 格式錯的值**原封往下丟**,讓 `parse_aware_datetime` 去回一致的 400
       —— 在這裡先擋一次會多出一種訊息不同的錯誤。
    """
    if not value or not value.strip():
        return None
    # 位移由 `FORM_TZ` 算出,不寫死字面 "+08:00" —— 兩個地方寫同一件事的話,
    # 改了一個而忘記另一個不會有錯誤訊息,只會差幾小時。
    offset = datetime(2000, 1, 1, tzinfo=FORM_TZ).strftime("%z")   # "+0800"
    return f"{value.strip()}{offset[:3]}:{offset[3:]}"


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
        # 🔴 與表單走**同一條**驗證+建立路徑(`_validate_and_create`)。
        #    各寫一份的話兩邊會漂移,而**漂移之後寬的那一邊贏**。
        announcement_id = _validate_and_create(
            author_sub=sub, title=payload.title, body=payload.body,
            audience=payload.audience,
            starts_at_raw=payload.starts_at, ends_at_raw=payload.ends_at,
        )
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



# 表單錯誤訊息用的欄位中文標籤。⚠ 只在**表單**這一層換 —— 見 `submit` 的註釋。
_FIELD_LABELS = {
    "title": "標題",
    "body": "內容",
    "starts_at": "生效時間",
    "ends_at": "失效時間",
    "audience": "收件範圍",
}


def _humanize(detail: str) -> str:
    """把驗證訊息裡的欄位名換成中文標籤。

    參數: detail — 例 `title 不得為空`
    回傳: 例 `標題 不得為空`
    副作用: 無
    ⚠ 只換**開頭**那個欄位名(訊息一律以欄位名開頭),避免把內容裡
      恰好同名的字一起換掉。
    """
    for name, label in _FIELD_LABELS.items():
        if detail.startswith(name):
            return label + detail[len(name):]
    return detail

def build_publish_page_router(*, settings) -> APIRouter:
    """公告發布頁(T09b):`GET` 表單 + `POST` 送出。

    參數: settings — Settings 快照(取 `base_path`)
    回傳: APIRouter(呼叫方負責加 `/inbox` 前綴)
    副作用: 無(只組 router)

    🔴 **與 API 分開成兩個 router 是刻意的:** API 在 `/api/v1` 之下、回 JSON、
       用 `Idempotency` 之類的機器語彙;這一頁回 HTML、有 CSRF、有可讀的錯誤訊息。
       混在一起的話「表單的 400」與「API 的 400」會被同一段程式處理,
       而其中一邊遲早會拿到不適合它的回應形狀。

    🔴 **這是本專案第一個 POST 表單,所以 CSRF 在這裡第一次成為必要。**
       在此之前所有 POST 都是 JSON API(跨站表單送不出 `application/json`)。
       `<form>` 一出現,任何網站都能指向我方端點。
    """
    router = APIRouter(tags=["announcements-ui"])
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

    def _render(request, *, sub: str, form: dict, error: str | None, status: int = 200):
        """算繪表單。

        🔴 `form` 一律回填使用者剛才填的值。清空表單等於叫他重打一次,
           而公告內容通常是一段字 —— 那是「錯誤處理把事情弄得更糟」。
        """
        return templates.TemplateResponse(
            request=request,
            name="announcement_new.html",
            status_code=status,
            context={
                "base_path": settings.base_path,
                # T10b:改用共用的 `csrf_token_for`(與後台同一條路)
                "csrf_token": csrf_token_for(request),
                "form": form,
                "error": error,
                # 🔴 顯示本人的 `sub` 而非姓名(§4.2a L1、DI-3 未裁決)
                "sub": sub,
            },
            headers={"Cache-Control": "no-store"},
        )

    _EMPTY = {"title": "", "body": "", "starts_at": "", "ends_at": ""}

    @router.get("/announcements/new", include_in_schema=False, response_class=HTMLResponse)
    def new_form(
        request: Request,
        identity=Depends(require_capability(CAP_PUBLISH_ANNOUNCEMENT)),
    ):
        """發布表單。

        能力: `publish_announcement` —— **`reader` 得到 403,不是 200 空表單**。
        🔴 回 200 空表單的話,他填完送出才被拒,而白費的那次輸入不會回來。
        """
        sub, _roles = identity
        return _render(request, sub=sub, form=dict(_EMPTY), error=None)

    @router.post("/announcements/new", include_in_schema=False)
    def submit(
        request: Request,
        title: str = Form(""),
        body: str = Form(""),
        audience: str = Form(AUDIENCE_ALL),
        starts_at: str = Form(""),
        ends_at: str = Form(""),
        _csrf=Depends(require_csrf),
        identity=Depends(require_capability(CAP_PUBLISH_ANNOUNCEMENT)),
    ):
        """送出表單。

        回傳: 成功 **303** 導回 `/inbox/`(讓他當場看到自己發的那則)
        錯誤: CSRF 不符 → **403**;值不合法 → **400 且重新算繪表單**
        副作用: 成功時 INSERT 一列 `announcement`

        🔴 **CSRF 先驗,而且驗在任何寫入之前。** 順序反過來的話,
           403 的回應旁邊那則公告已經廣播給全公司了。
        🔴 為什麼成功是 **303 而不是 200**:POST 之後留在 POST 的結果頁上,
           使用者按重新整理就會**再發一則**。303 讓瀏覽器改用 GET 重新載入。
        """
        sub, _roles = identity
        # 🔴 CSRF 由 `Depends(require_csrf)` 在**進到這裡之前**驗完(T10b)。
        #    原本寫在這裡的手動比對已移除 —— 手寫的版本只保護了「有人想到的」
        #    那個表單,而角色後台那兩個從 T05 就沒有(見 T10b dev-log)。
        filled = {
            "title": title, "body": body,
            "starts_at": starts_at, "ends_at": ends_at,
        }
        try:
            announcement_id = _validate_and_create(
                author_sub=sub, title=title, body=body, audience=audience,
                starts_at_raw=_form_time_to_iso(starts_at),
                ends_at_raw=_form_time_to_iso(ends_at),
            )
        except BadRequest as exc:
            # 🔴 具體訊息 + 回填已填的值。「發布失敗」四個字會讓使用者只能亂試。
            # ⚠ 共用的驗證函式用**欄位名**(`title` / `starts_at`)寫訊息 —— 那是給
            #   API 呼叫方看的。這一頁的讀者是人,所以在這裡換成欄位的中文標籤;
            #   不在共用函式裡改,否則 API 的錯誤訊息會變成人話而機器不好比對。
            return _render(request, sub=sub, form=filled,
                           error=_humanize(exc.detail), status=400)

        log_event("announcement_published", sub=sub, announcement_id=announcement_id,
                  via="form")
        return RedirectResponse(f"{settings.base_path}/", status_code=303)

    return router
