# -*- coding: utf-8 -*-
"""T09 公告紅測試(發布 / 有效期 / active / 逐人已讀)。

對應驗收:`docs/任務表.md` T09、`docs/dev-logs/2026-08-25_T09_公告.md` 的七條。

🔴 這一組的每一條都對應一種**不會有錯誤訊息**的失敗:

| 驗收 | 壞掉時的症狀 |
|---|---|
| 非 announcer 發布 → 403 **且零列寫入** | 只驗狀態碼的話,「先寫入再回 403」照樣綠 |
| 未來的公告不得出現在 active | 只驗 `ends_at` 是最容易漏的一半:排程下週的公告當場就出現 |
| 甲讀過不會讓乙變已讀 | 已讀若寫在公告本身,一個人讀完全公司都變已讀,其他人只是覺得自己好像看過 |
| `ends_at <= starts_at` → 400 | 空窗公告=發布成功但永遠不出現,而回應是 200 |
| 不帶時區的時間 → 400 | 台灣的 `02:00` 存成 UTC 差 8 小時,公告晚 8 小時出現而零錯誤 |
| 回應零人名、零 `author_sub` | DI-3 未裁決前顯示姓名=違反契約 §4.2a L1 |
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from tests.conftest import _login

SELF_SUB = "11111111-2222-3333-4444-555555555555"   # 替身簽出的 sub
OTHER_SUB = "99999999-8888-7777-6666-555555555555"

ACTIVE = "/inbox/api/v1/announcements/active"
PUBLISH = "/inbox/api/v1/announcements"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _grant(db_session, sub: str, role: str) -> None:
    """直接在資料庫授與角色(fixture 假資料,不經後台 UI)。

    副作用: INSERT `app_user`(必要時)與 `user_role`
    ⚠ 角色是**每次請求**才查的(`app/deps.py`),所以登入之後才授與也會即時生效
    ——這正是那個設計要保證的事,順便被本檔用到。
    """
    from app.models import AppUser
    from app.repo import grant_role

    if db_session.get(AppUser, sub) is None:
        db_session.add(AppUser(sub=sub, is_active=True))
        db_session.flush()
    grant_role(db_session, sub, role, granted_by="test")
    db_session.commit()


def _make_announcement(db_session, *, title="公告主旨", body="公告內容",
                       starts_at=None, ends_at=None, author=SELF_SUB):
    """在資料庫直接造一則公告(不經 API)。

    回傳: Announcement
    副作用: INSERT 一列 `announcement`
    刻意不經 API:有效期的邊界要能造出「已過期」「還沒開始」這種
    發布端點**不接受**的值(它會 400),而那正是本檔要驗的查詢條件。
    """
    from app.models import AUDIENCE_ALL, Announcement

    row = Announcement(
        id=uuid.uuid4(),
        author_sub=author,
        title=title,
        body=body,
        audience=AUDIENCE_ALL,
        starts_at=starts_at or (_now() - timedelta(hours=1)),
        ends_at=ends_at,
    )
    db_session.add(row)
    db_session.flush()
    db_session.commit()
    return row


def _count_announcements(db_session) -> int:
    from sqlalchemy import func, select

    from app.models import Announcement

    db_session.expire_all()
    return int(db_session.scalar(select(func.count()).select_from(Announcement)) or 0)


# ═══════════════════════════════════════════════════════════════════
# 1. 🔴 發布權限:非 announcer 一律 403,而且什麼都不能寫進去
# ═══════════════════════════════════════════════════════════════════
def test_reader_cannot_publish_and_writes_nothing(app_client, db_session):
    """`reader` 發布公告 → 403,**且資料庫零新增列**。

    🔴 「零新增列」是這條的重點。只斷言狀態碼的話,一個
    「先寫進去、再回 403」的實作會照樣綠 —— 而公告已經在表上了,
    下一個查詢就會把它端出來,**沒有任何錯誤訊息**。
    這也是 DEC-16 條件 C3 的反向測試之一(`reader` 不得發公告)。
    """
    http, transport = app_client
    _login(http, transport)
    before = _count_announcements(db_session)

    r = http.post(PUBLISH, json={"title": "偷發的公告", "body": "內容"})

    assert r.status_code == 403, f"預期 403,實得 {r.status_code} {r.text[:200]}"
    assert _count_announcements(db_session) == before, "🔴 403 卻寫進了一列公告"


def test_announcer_can_publish(app_client, db_session):
    """有 `announcer` 就發得出去,且回應帶得回 id。"""
    http, transport = app_client
    _login(http, transport)
    from app.authz import ROLE_ANNOUNCER

    _grant(db_session, SELF_SUB, ROLE_ANNOUNCER)

    r = http.post(PUBLISH, json={"title": "系統維護通知", "body": "8/30 02:00–04:00"})

    assert r.status_code == 201, f"預期 201,實得 {r.status_code} {r.text[:300]}"
    assert uuid.UUID(r.json()["id"]), "回應應帶回公告 id"
    assert _count_announcements(db_session) == 1


def test_author_sub_comes_from_token_not_from_body(app_client, db_session):
    """body 裡的 `author_sub` **一律不採信**(本專案紅線:身分不信前端)。

    🔴 採信的話,任何有 `announcer` 的人都能冒名發公告,
    而稽核欄位上寫的是被冒名的那個人 —— 事後完全查不出來。
    """
    http, transport = app_client
    _login(http, transport)
    from app.authz import ROLE_ANNOUNCER
    from app.models import Announcement

    _grant(db_session, SELF_SUB, ROLE_ANNOUNCER)

    r = http.post(PUBLISH, json={
        "title": "冒名測試", "body": "內容", "author_sub": OTHER_SUB,
    })
    assert r.status_code == 201, f"預期 201,實得 {r.status_code} {r.text[:200]}"

    db_session.expire_all()
    row = db_session.get(Announcement, uuid.UUID(r.json()["id"]))
    assert row.author_sub == SELF_SUB, f"🔴 author_sub 被前端指定了:{row.author_sub}"


# ═══════════════════════════════════════════════════════════════════
# 2. 🔴 有效期:兩邊都要驗,而「還沒開始」是最容易漏的那一半
# ═══════════════════════════════════════════════════════════════════
def test_expired_announcement_not_in_active(app_client, db_session):
    """`ends_at` 已過的公告不得出現在 active。"""
    http, transport = app_client
    _login(http, transport)
    gone = _make_announcement(
        db_session, title="過期公告",
        starts_at=_now() - timedelta(days=2), ends_at=_now() - timedelta(days=1),
    )
    live = _make_announcement(db_session, title="有效公告")

    r = http.get(ACTIVE)
    assert r.status_code == 200, f"預期 200,實得 {r.status_code} {r.text[:200]}"
    ids = {a["id"] for a in r.json()["items"]}
    assert str(live.id) in ids, "有效公告沒出現"
    assert str(gone.id) not in ids, "🔴 過期公告出現在 active"
    assert "過期公告" not in r.text, "🔴 過期公告的標題出現在回應裡"


def test_future_announcement_not_in_active(app_client, db_session):
    """`starts_at` 還沒到的公告不得出現在 active。

    🔴 這是最容易漏的一半:只驗 `ends_at` 的實作會讓「排程下週」的公告
    **當場就出現**,而發布者以為排程生效了 —— 沒有任何錯誤訊息。
    """
    http, transport = app_client
    _login(http, transport)
    future = _make_announcement(
        db_session, title="下週才開始", starts_at=_now() + timedelta(days=7),
    )

    r = http.get(ACTIVE)
    ids = {a["id"] for a in r.json()["items"]}
    assert str(future.id) not in ids, "🔴 尚未開始的公告出現在 active"
    assert "下週才開始" not in r.text


def test_null_ends_at_means_no_expiry(app_client, db_session):
    """`ends_at` 為 NULL = 無期限,必須持續有效。"""
    http, transport = app_client
    _login(http, transport)
    forever = _make_announcement(db_session, title="長期公告", ends_at=None)

    r = http.get(ACTIVE)
    assert str(forever.id) in {a["id"] for a in r.json()["items"]}


def test_window_boundary_start_inclusive_end_exclusive(db_session):
    """邊界語意釘死:`starts_at <= now < ends_at`。

    🔴 用注入的 `now` 而不是真實時鐘 —— 邊界斷言用 `datetime.now()` 會
    隨機失敗,而失敗看起來像程式錯。
    ⚠ 這個語意與 `app/models.py` 裡 `ix_announcement_window` 的註釋
    **必須一致**;兩邊漂移的話索引還在、查詢也還跑得動,只是條件不同了。
    """
    from app.repo import list_active_announcements

    t = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    starting_now = _make_announcement(db_session, title="剛好開始", starts_at=t)
    ending_now = _make_announcement(
        db_session, title="剛好結束", starts_at=t - timedelta(days=1), ends_at=t,
    )

    ids = {a.id for a, _read in list_active_announcements(db_session, user_sub=SELF_SUB, now=t)}
    assert starting_now.id in ids, "🔴 starts_at == now 應視為已開始(含)"
    assert ending_now.id not in ids, "🔴 ends_at == now 應視為已結束(不含)"


# ═══════════════════════════════════════════════════════════════════
# 3. 🔴 寫入驗證:兩種「發布成功但永遠看不到」的輸入必須當場擋掉
# ═══════════════════════════════════════════════════════════════════
def test_inverted_window_is_rejected_400(app_client, db_session):
    """`ends_at <= starts_at` → 400。

    🔴 不擋的話,發布回 200、資料也進去了,而那則公告**永遠不會出現**
    ——發布者沒有任何線索知道自己按下的那個按鈕沒有作用。
    """
    http, transport = app_client
    _login(http, transport)
    from app.authz import ROLE_ANNOUNCER

    _grant(db_session, SELF_SUB, ROLE_ANNOUNCER)
    t = _now()

    r = http.post(PUBLISH, json={
        "title": "空窗公告", "body": "內容",
        "starts_at": t.isoformat(), "ends_at": (t - timedelta(hours=1)).isoformat(),
    })
    assert r.status_code == 400, f"預期 400,實得 {r.status_code} {r.text[:200]}"
    assert _count_announcements(db_session) == 0, "🔴 400 卻仍寫入了一列"


def test_naive_datetime_is_rejected_400(app_client, db_session):
    """不帶時區的時間 → 400。

    🔴 台灣同事輸入 `2026-08-30T02:00:00` 想的是 UTC+8,存成 UTC 就差 8 小時
    ——公告晚 8 小時才出現,而**沒有任何錯誤訊息**。
    portal 憲法第四條7 記載的就是同一種坑(log 是 UTC、文件是 UTC+8,
    標錯會把同一秒的兩筆事件讀成不相干)。
    """
    http, transport = app_client
    _login(http, transport)
    from app.authz import ROLE_ANNOUNCER

    _grant(db_session, SELF_SUB, ROLE_ANNOUNCER)

    r = http.post(PUBLISH, json={
        "title": "沒帶時區", "body": "內容", "starts_at": "2026-08-30T02:00:00",
    })
    assert r.status_code == 400, f"預期 400,實得 {r.status_code} {r.text[:200]}"
    assert _count_announcements(db_session) == 0


def test_unknown_audience_is_rejected_400(app_client, db_session):
    """`audience` 只接受 `all`;群組定向做不到,必須當場拒收。

    🔴 收下來的話會撞到資料庫的 CHECK → **500**,呼叫方讀到的是
    「伺服器壞了」而不是「這個值不支援」。
    ⚠ 更糟的是若連 CHECK 都拿掉:發布者以為自己設定了收件範圍,
    而每一則其實都送給所有人(見 `app/models.py` 的 `AUDIENCES`)。
    """
    http, transport = app_client
    _login(http, transport)
    from app.authz import ROLE_ANNOUNCER

    _grant(db_session, SELF_SUB, ROLE_ANNOUNCER)

    r = http.post(PUBLISH, json={
        "title": "群組公告", "body": "內容", "audience": "group:QA",
    })
    assert r.status_code == 400, f"預期 400,實得 {r.status_code} {r.text[:200]}"
    assert _count_announcements(db_session) == 0


def test_blank_title_is_rejected_400(app_client, db_session):
    """空白標題 → 400(只有空白字元也算空)。"""
    http, transport = app_client
    _login(http, transport)
    from app.authz import ROLE_ANNOUNCER

    _grant(db_session, SELF_SUB, ROLE_ANNOUNCER)

    r = http.post(PUBLISH, json={"title": "   ", "body": "內容"})
    assert r.status_code == 400, f"預期 400,實得 {r.status_code} {r.text[:200]}"
    assert _count_announcements(db_session) == 0


# ═══════════════════════════════════════════════════════════════════
# 4. 🔴 逐人已讀:一則對多人,已讀不是公告的屬性
# ═══════════════════════════════════════════════════════════════════
def test_mark_read_is_idempotent_and_one_row_per_person(app_client, db_session):
    """重複標已讀:不複製列、`read_at` 不被覆寫。

    🔴 覆寫的話「這則是什麼時候讀的」永遠是最後一次點擊的時間,
    那個欄位就沒有意義了 —— 而兩次呼叫都回 200,不會有任何錯誤訊息。
    🔴 複製列的話**畫面上完全正常**(已讀就是已讀),只有列數在悄悄長。
    """
    from sqlalchemy import func, select

    from app.models import AnnouncementRead

    http, transport = app_client
    _login(http, transport)
    ann = _make_announcement(db_session)

    r1 = http.post(f"{PUBLISH}/{ann.id}/read")
    assert r1.status_code == 200, f"預期 200,實得 {r1.status_code} {r1.text[:200]}"
    first_read_at = r1.json()["read_at"]

    r2 = http.post(f"{PUBLISH}/{ann.id}/read")
    assert r2.status_code == 200
    assert r2.json()["read_at"] == first_read_at, "🔴 第二次呼叫覆寫了 read_at"

    db_session.expire_all()
    n = db_session.scalar(
        select(func.count()).select_from(AnnouncementRead)
        .where(AnnouncementRead.announcement_id == ann.id)
    )
    assert n == 1, f"🔴 逐人一列被複製成 {n} 列"


def test_read_by_one_person_does_not_mark_it_read_for_others(app_client, db_session):
    """甲讀過,乙的那則仍然是未讀。

    🔴 這條擋的是「把已讀寫在公告本身」——一個人讀完,全公司都變成已讀,
    而其他人只是覺得自己**好像看過**。沒有任何人會回報這是 bug。
    """
    from app.models import AppUser
    from app.repo import list_active_announcements

    http, transport = app_client
    _login(http, transport)
    ann = _make_announcement(db_session)
    db_session.add(AppUser(sub=OTHER_SUB, is_active=True))
    db_session.commit()

    assert http.post(f"{PUBLISH}/{ann.id}/read").status_code == 200

    db_session.expire_all()
    mine = dict((a.id, read) for a, read in
                list_active_announcements(db_session, user_sub=SELF_SUB))
    theirs = dict((a.id, read) for a, read in
                  list_active_announcements(db_session, user_sub=OTHER_SUB))
    assert mine[ann.id] is True, "自己標過的公告應為已讀"
    assert theirs[ann.id] is False, "🔴 別人讀過就把我的那則也變成已讀了"


def test_active_reports_my_own_read_state(app_client, db_session):
    """`active` 的 `is_read` 是**我**的狀態,且未讀數只數我沒讀的。"""
    http, transport = app_client
    _login(http, transport)
    a1 = _make_announcement(db_session, title="第一則")
    _make_announcement(db_session, title="第二則")

    assert http.get(ACTIVE).json()["unread"] == 2
    http.post(f"{PUBLISH}/{a1.id}/read")

    body = http.get(ACTIVE).json()
    assert body["unread"] == 1, f"未讀數應為 1,實得 {body['unread']}"
    states = {a["id"]: a["is_read"] for a in body["items"]}
    assert states[str(a1.id)] is True


def test_mark_read_unknown_announcement_is_404(app_client, db_session):
    """不存在的公告 → 404。

    ⚠ 與訊息的 403 **刻意不同**:訊息有「這是不是你的」這個問題,
    答 404 會洩漏「這個 id 存在」;公告對所有人都可見,沒有歸屬可保護,
    此時 404 才是誠實的答案(403 會讓人以為是自己權限不足)。
    """
    http, transport = app_client
    _login(http, transport)
    r = http.post(f"{PUBLISH}/{uuid.uuid4()}/read")
    assert r.status_code == 404, f"預期 404,實得 {r.status_code} {r.text[:200]}"


# ═══════════════════════════════════════════════════════════════════
# 5. 🔴 零人名 + 401/403 分界
# ═══════════════════════════════════════════════════════════════════
def test_active_response_has_no_author_identity(app_client, db_session):
    """`active` 的回應不得帶 `author_sub`,也不得帶任何姓名。

    🔴 先把 L1 快取寫上姓名再斷言「不出現」——不先寫入的話,
    快取為空時必然通過=**假綠**(T08 踩過同一個,已記在其 dev-log)。
    🔴 同時做**結構**斷言:任何含 `name` 的鍵、或 `author` 開頭的鍵都不得存在。
    只比對字串的話,回應多一個 `author_name: null` 也會通過,
    而那個欄位一旦存在,前端就會拿它去查姓名。
    """
    from app.models import AppUser

    http, transport = app_client
    _login(http, transport)
    db_session.expire_all()
    user = db_session.get(AppUser, SELF_SUB)
    user.display_name = "陳測試"
    db_session.commit()
    _make_announcement(db_session, title="公告", author=SELF_SUB)

    r = http.get(ACTIVE)
    assert "陳測試" not in r.text, "🔴 姓名出現在一般使用者可見的回應裡"
    assert SELF_SUB not in r.text, "🔴 author_sub 出現在回應裡"
    for item in r.json()["items"]:
        for key in item:
            assert "name" not in key.lower(), f"🔴 回應有姓名類欄位:{key}"
            assert not key.startswith("author"), f"🔴 回應有作者身分欄位:{key}"


def test_announcement_endpoints_require_login_401(app_client):
    """未登入 → **401**(不是 403)。

    🔴 401=沒登入、403=登入了但沒權限,兩者不得混用(平台紅線)。
    """
    http, _transport = app_client
    assert http.get(ACTIVE).status_code == 401
    assert http.post(f"{PUBLISH}/{uuid.uuid4()}/read").status_code == 401
    assert http.post(PUBLISH, json={"title": "t", "body": "b"}).status_code == 401


def test_zero_role_user_cannot_read_announcements_403(app_client_no_autogrant, db_session):
    """沒有 `reader` 的人看公告 → 403(核可條件 C2/C4 的落點)。

    停用 `reader` 之後連公告都看不到 —— 「還能不能用」由本專案後台決定,
    不押在 IdP 帳號狀態上。
    """
    http, transport = app_client_no_autogrant
    _login(http, transport)
    assert http.get(ACTIVE).status_code == 403


# ═══════════════════════════════════════════════════════════════════
# 6. 收件匣頁要真的把公告畫出來(計畫段第 4 項)
# ═══════════════════════════════════════════════════════════════════
def test_inbox_page_shows_active_announcements(app_client, db_session):
    """收件匣頁顯示有效公告,不顯示過期的。

    🔴 沒有這一條的話,公告上線之後**沒有任何畫面顯示它**,
    而發布 API 會回 200、`active` 也回得出來 —— 零錯誤訊息的功能缺口。
    """
    http, transport = app_client
    _login(http, transport)
    _make_announcement(db_session, title="現在有效的公告", body="請於 8/30 前完成")
    _make_announcement(
        db_session, title="早就過期的公告",
        starts_at=_now() - timedelta(days=3), ends_at=_now() - timedelta(days=2),
    )

    r = http.get("/inbox/")
    assert r.status_code == 200, f"預期 200,實得 {r.status_code}"
    assert "現在有效的公告" in r.text, "🔴 有效公告沒有出現在收件匣頁"
    assert "早就過期的公告" not in r.text, "🔴 過期公告出現在收件匣頁"


def test_inbox_page_announcement_has_no_names(app_client, db_session):
    """收件匣頁的公告區塊同樣**零人名**(§4.2a L1)。"""
    from app.models import AppUser

    http, transport = app_client
    _login(http, transport)
    db_session.expire_all()
    db_session.get(AppUser, SELF_SUB).display_name = "陳測試"
    db_session.commit()
    _make_announcement(db_session, title="公告", author=SELF_SUB)

    r = http.get("/inbox/")
    assert "陳測試" not in r.text, "🔴 姓名出現在收件匣頁的公告區塊"


# ═══════════════════════════════════════════════════════════════════
# 7. 🔴 輸出的時間必須帶時區(與輸入對稱)
# ═══════════════════════════════════════════════════════════════════
def test_every_timestamp_in_api_responses_carries_a_timezone(app_client, db_session):
    """公告與訊息的**每一個**時間欄位都必須帶時區位移。

    🔴 我方拒收不帶時區的**輸入**(`test_naive_datetime_is_rejected_400`),
       理由是「差 8 小時而沒有錯誤訊息」;那就不能吐出不帶時區的**輸出**
       —— 那是把同一個坑挖給呼叫方。

    ⚠ 這條同時涵蓋 T08 的訊息端點:在 **SQLite** 上 `.isoformat()` 吐的是
      naive 字串(它不存時區),而正式環境的 PostgreSQL `timestamptz` 會保留
      —— 也就是**本機與正式行為不同**,正是 `tests/conftest.py` 記載的那個盲點。
      T09 發現時一併修掉兩邊。
    """
    from tests.test_inbox import _make_message

    http, transport = app_client
    _login(http, transport)
    ann = _make_announcement(db_session)
    msg = _make_message(db_session, recipient=SELF_SUB)
    db_session.commit()
    http.post(f"{PUBLISH}/{ann.id}/read")
    http.post(f"/inbox/api/v1/messages/{msg.id}/read")

    payloads = [
        http.get(ACTIVE).json()["items"],
        http.get("/inbox/api/v1/messages").json()["items"],
    ]
    checked = 0
    for items in payloads:
        for item in items:
            for key, value in item.items():
                if not key.endswith("_at") or value is None:
                    continue
                checked += 1
                parsed = datetime.fromisoformat(value)
                assert parsed.tzinfo is not None, f"🔴 {key} 沒有時區:{value}"
    assert checked >= 4, f"應該至少檢查到 4 個時間欄位,實際 {checked}"
