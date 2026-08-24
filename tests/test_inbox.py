# -*- coding: utf-8 -*-
"""T08 讀取 API + 收件匣 UI 紅測試。

對應驗收:`docs/任務表.md` T08、本專案紅線(收件權限後端判定、`sender_sub`
不信前端、階段一零人名)、契約 §4.2a L1(姓名不得出現在一般使用者可見頁面)。

🔴 這一組測試的四項驗收各自對應一個**不會有錯誤訊息**的洩漏:

| 驗收 | 壞掉時的症狀 |
|---|---|
| 讀他人訊息 → 403 | 列表少一個 `WHERE` 就把全公司的通知端給任何人,而**畫面看起來完全正常**(就是訊息比較多) |
| `sender_sub` 不信前端 | 任何人都能冒充任何人寄信,而收件人看不出差別 |
| UI 零外部 CDN | 沒網路時整頁沒樣式;CSP 上線後被擋 |
| 階段一零人名 | DI-3 未裁決前顯示姓名=**違反契約 §4.2a L1** |
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from tests.conftest import _login

OTHER_SUB = "99999999-8888-7777-6666-555555555555"
SELF_SUB = "11111111-2222-3333-4444-555555555555"   # 替身簽出的 sub


def _make_message(session, *, recipient: str, subject="測試主旨", is_read=False,
                  source_app="workorder", body="測試內容"):
    """在資料庫直接造一筆訊息(fixture 假資料,不經 API)。

    回傳: Message
    副作用: INSERT 一列 `message`
    刻意不經 API:寄信端點是 T09/T14 的事,而本任務要測的是**讀**。
    """
    from app.models import CATEGORY_SYSTEM, Message

    row = Message(
        id=uuid.uuid4(),
        recipient_sub=recipient,
        sender_sub=None,
        category=CATEGORY_SYSTEM,
        subject=subject,
        body=body,
        source_app=source_app,
        is_read=is_read,
        created_at=datetime.now(timezone.utc),
    )
    session.add(row)
    session.flush()
    return row


# ═══════════════════════════════════════════════════════════════════
# 1. 🔴 只看得到自己的
# ═══════════════════════════════════════════════════════════════════
def test_list_returns_only_own_messages(app_client, db_session):
    """列表只回自己的訊息——別人的**不得出現**。

    🔴 這條擋的是「列表少一個 `WHERE recipient_sub = 自己`」。
    那個疏漏把全公司的通知端給任何登入的人,而**畫面看起來完全正常**
    ——只是訊息比較多,而沒有人會覺得「我的通知變多了」是個 bug。
    """
    http, transport = app_client
    _login(http, transport)
    mine = _make_message(db_session, recipient=SELF_SUB, subject="給我的")
    theirs = _make_message(db_session, recipient=OTHER_SUB, subject="給別人的")
    db_session.commit()

    r = http.get("/inbox/api/v1/messages")
    assert r.status_code == 200, f"預期 200,實得 {r.status_code} {r.text[:200]}"
    ids = {m["id"] for m in r.json()["items"]}
    assert str(mine.id) in ids, "自己的訊息沒有出現在列表裡"
    assert str(theirs.id) not in ids, "🔴 別人的訊息出現在我的列表裡"

    # 連主旨都不得洩漏(斷言 id 不夠——回應可能用別的欄位帶出內容)
    assert "給別人的" not in r.text


def test_mark_read_on_others_message_is_403(app_client, db_session):
    """拿別人訊息的 id 標已讀 → **403**,且那筆訊息**不得被改動**。

    ⚠ 為什麼是 403 而不是 404:訊息 id 是 **UUIDv4,猜不到** ——
    能打到這個端點表示你已經有那個 id 了,所以 403 沒有多洩漏什麼;
    而 401/403/404 三者混用會讓查問題的人分不出
    「沒登入 / 沒權限 / 不存在」。
    🔴 若日後 id 改成可猜的形式(序號),這個決定必須改回 404。

    後半同樣重要:**斷言那筆訊息沒有被改動**。只回 403 而順手把
    `is_read` 寫下去的話,擋了門卻已經動到別人的資料。
    """
    from app.models import Message

    http, transport = app_client
    _login(http, transport)
    theirs = _make_message(db_session, recipient=OTHER_SUB)
    db_session.commit()

    r = http.post(f"/inbox/api/v1/messages/{theirs.id}/read")
    assert r.status_code == 403, f"標別人的訊息應 403,實得 {r.status_code}"

    db_session.expire_all()
    row = db_session.get(Message, theirs.id)
    assert row.is_read is False, "🔴 回了 403 卻還是把別人的訊息標成已讀了"
    assert row.read_at is None


def test_unread_count_counts_only_own_unread(app_client, db_session):
    """未讀數只算自己的未讀(30 秒輪詢的那個端點)。"""
    http, transport = app_client
    _login(http, transport)
    _make_message(db_session, recipient=SELF_SUB, is_read=False)
    _make_message(db_session, recipient=SELF_SUB, is_read=False)
    _make_message(db_session, recipient=SELF_SUB, is_read=True)
    _make_message(db_session, recipient=OTHER_SUB, is_read=False)
    db_session.commit()

    r = http.get("/inbox/api/v1/messages/unread-count")
    assert r.status_code == 200
    assert r.json()["unread"] == 2, f"預期 2,實得 {r.json()}"


def test_unread_filter_returns_only_unread(app_client, db_session):
    """`?unread=true` 只回未讀的。"""
    http, transport = app_client
    _login(http, transport)
    unread = _make_message(db_session, recipient=SELF_SUB, is_read=False)
    read = _make_message(db_session, recipient=SELF_SUB, is_read=True)
    db_session.commit()

    ids = {m["id"] for m in http.get("/inbox/api/v1/messages?unread=true").json()["items"]}
    assert str(unread.id) in ids
    assert str(read.id) not in ids


# ═══════════════════════════════════════════════════════════════════
# 2. 🔴 冪等的具體語意
# ═══════════════════════════════════════════════════════════════════
def test_mark_read_is_idempotent_and_preserves_read_at(app_client, db_session):
    """第二次標已讀必須**不改變 `read_at`**——不是「不報錯」而已。

    🔴 `read_at` 被第二次呼叫覆寫的話,「這則是什麼時候讀的」就永遠是
    最後一次點擊的時間,而那個欄位存在的意義就沒了。
    而它不會有任何錯誤訊息——兩次呼叫都回 200。
    """
    from app.models import Message

    http, transport = app_client
    _login(http, transport)
    msg = _make_message(db_session, recipient=SELF_SUB)
    db_session.commit()

    assert http.post(f"/inbox/api/v1/messages/{msg.id}/read").status_code == 200
    db_session.expire_all()
    first = db_session.get(Message, msg.id).read_at
    assert first is not None, "第一次標已讀沒有寫 read_at"

    assert http.post(f"/inbox/api/v1/messages/{msg.id}/read").status_code == 200
    db_session.expire_all()
    assert db_session.get(Message, msg.id).read_at == first, (
        "🔴 第二次標已讀覆寫了 read_at ——「什麼時候讀的」就變成最後一次點擊的時間"
    )


def test_read_endpoints_require_read_own_capability(app_client_no_autogrant, db_session):
    """零角色使用者打讀取端點一律 **403**(不是 401)。

    401 = 憑證無效;403 = 已認證但無此權限。混用會讓人查錯方向。
    """
    http, transport = app_client_no_autogrant
    _login(http, transport)
    msg = _make_message(db_session, recipient=SELF_SUB)
    db_session.commit()

    assert http.get("/inbox/api/v1/messages").status_code == 403
    assert http.get("/inbox/api/v1/messages/unread-count").status_code == 403
    assert http.post(f"/inbox/api/v1/messages/{msg.id}/read").status_code == 403


def test_unauthenticated_read_is_401(app_client):
    """未登入打讀取端點 → **401**(與上一條的 403 分開)。"""
    http, _ = app_client
    assert http.get("/inbox/api/v1/messages").status_code == 401
    assert http.get("/inbox/api/v1/messages/unread-count").status_code == 401


# ═══════════════════════════════════════════════════════════════════
# 3. 🔴 身分欄位不信前端
# ═══════════════════════════════════════════════════════════════════
def test_recipient_sub_from_query_is_ignored(app_client, db_session):
    """前端傳 `recipient_sub` 一律**不採信**(本專案紅線)。

    🔴 這條測的是「後端有沒有在某個地方接受前端指定的收件人」。
    接受了的話,任何人加一個查詢參數就能讀別人的收件匣,
    而**那個請求看起來完全正常**。
    """
    http, transport = app_client
    _login(http, transport)
    theirs = _make_message(db_session, recipient=OTHER_SUB, subject="別人的秘密")
    db_session.commit()

    for params in ({"recipient_sub": OTHER_SUB}, {"sub": OTHER_SUB}, {"user": OTHER_SUB}):
        r = http.get("/inbox/api/v1/messages", params=params)
        assert r.status_code == 200, f"{params} → {r.status_code}"
        assert str(theirs.id) not in r.text, f"🔴 {params} 讓我讀到了別人的訊息"
        assert "別人的秘密" not in r.text


# ═══════════════════════════════════════════════════════════════════
# 4. 收件匣頁
# ═══════════════════════════════════════════════════════════════════
def test_inbox_page_renders_for_reader(app_client, db_session):
    """收件匣頁對 `reader` 回 200,並顯示自己的訊息主旨與 `source_app` 標籤。"""
    http, transport = app_client
    _login(http, transport)
    _make_message(db_session, recipient=SELF_SUB, subject="工單已指派", source_app="workorder")
    db_session.commit()

    r = http.get("/inbox/")
    assert r.status_code == 200, f"預期 200,實得 {r.status_code}"
    assert "工單已指派" in r.text
    assert "workorder" in r.text, "缺 source_app 標籤(階段一用它取代人名)"


def test_inbox_page_has_no_personal_names(app_client, db_session):
    """🔴 階段一收件匣頁**零人名**——即使 L1 快取裡**有**姓名也不得出現。

    契約 §4.2a L1 明文:顯示名稱快取**不得出現在一般使用者可見頁面**。
    DI-3(同儕可見的顯示名稱)portal **尚未裁決**,故階段一刻意零人名。

    🔴 **測法是刻意的:先把姓名寫進快取,再斷言它不出現。**
    直接斷言「頁面上沒有姓名」在快取為空時**必然通過** —— 那是假綠。
    (與 T04 那支 `at_hash` 假綠測試同型;這次是動工前就想到的。)
    """
    from app.models import AppUser

    http, transport = app_client
    _login(http, transport)

    user = db_session.get(AppUser, SELF_SUB)
    assert user is not None, "前置:首登應已建號"
    assert user.display_name, "前置:替身給了 name claim,L1 快取應已寫入"
    cached_name = user.display_name

    _make_message(db_session, recipient=SELF_SUB, subject="一則通知")
    db_session.commit()

    r = http.get("/inbox/")
    assert r.status_code == 200
    assert cached_name not in r.text, (
        f"🔴 收件匣頁出現了快取的姓名 {cached_name!r} —— 違反契約 §4.2a L1,"
        "且 DI-3 尚未裁決"
    )

    # 🔴 **API 回應也是「一般使用者可見」的面。** 這一段是突變檢查補上的:
    #    原本只驗 HTML 頁面,而往 `_serialize` 加一個姓名欄位**測試照樣綠**
    #    ——因為模板沒有算繪它。但前端 JS 拿得到,而那就是 §4.2a L1 要禁的。
    api = http.get("/inbox/api/v1/messages")
    assert api.status_code == 200
    assert cached_name not in api.text, (
        f"🔴 列表 API 回應裡出現了快取的姓名 {cached_name!r}"
    )

    # 結構性斷言:回應**不得有任何看起來像姓名的鍵**。
    # 它擋的是「加了欄位但值剛好不是這次快取的那個字串」——
    # 上面那條比字串的斷言抓不到那種情形,而它同樣是洩漏。
    for item in api.json()["items"]:
        offenders = [k for k in item if "name" in k.lower()]
        assert not offenders, (
            f"🔴 列表 API 的項目帶了姓名類欄位:{offenders}。"
            "階段一零人名;要露出寄件人請先等 DI-3 裁決"
        )
        # `sender_sub` 也刻意不輸出:把它放進回應等於預告「階段二這裡會有
        # 一個人的 id」,而前端拿它去查姓名就繞過了 DI-3 的裁決。
        assert "sender_sub" not in item, "🔴 回應輸出了 sender_sub(見 _serialize 的說明)"


def test_inbox_page_has_zero_external_resources(app_client):
    """收件匣頁不得引用任何外部主機的資源(契約 §4.10 禁外部 CDN)。"""
    http, transport = app_client
    _login(http, transport)
    body = http.get("/inbox/").text
    for bad in ("https://cdn", "http://cdn", "https://fonts", "//unpkg", "jsdelivr",
                "cdnjs", "googleapis"):
        assert bad not in body, f"引用了外部資源:{bad}"
    assert "prefers-color-scheme: dark" not in body, "第四條2:不得深色自動切換"
