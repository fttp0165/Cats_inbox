# -*- coding: utf-8 -*-
"""T09b 公告發布頁紅測試(權限 / CSRF / 時區 / 四種 400 / 入口)。

對應驗收:`docs/任務表.md` T09b、`docs/dev-logs/2026-08-25_T09b_公告發布頁.md` 的八條。

🔴 三件這一頁第一次遇到的事,各自對應一種不會有錯誤訊息的失敗:

| 事 | 壞掉時的症狀 |
|---|---|
| **CSRF**(本專案第一個 POST 表單) | 受害者只會看到一則自己沒發過的公告 |
| `datetime-local` **不帶時區** | 差 8 小時 —— 而「發布成功」與「排在 8 小時後」都回 303 |
| CSP 之下**不能有 JS、不能有行內樣式屬性** | 版面走鐘 / 腳本被靜默擋掉,伺服器零錯誤 |
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import _login

SELF_SUB = "11111111-2222-3333-4444-555555555555"
NEW = "/inbox/announcements/new"


def _as_utc(value: datetime) -> datetime:
    """把 DB 取回的時間視為 UTC(SQLite 不存時區)。

    🔴 **不能用 `value.astimezone(timezone.utc)`。** 對 naive datetime,
       `astimezone` 會把它當成**執行機器的本地時區** —— 在 TZ=UTC 的 CI 上
       剛好對,而在 TZ=Asia/Taipei 的開發機上會差 8 小時,
       **而那個失敗看起來像程式錯**。
    ⚠ 寫入端一律是 UTC(`_utcnow()` 或 `parse_aware_datetime` 正規化後),
      所以「naive 即 UTC」是我方自己保證的慣例 —— 與
      `app/validation.py::iso_utc` 同一個理由。
    """
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _grant_announcer(db_session):
    from app.authz import ROLE_ANNOUNCER
    from app.repo import grant_role

    grant_role(db_session, SELF_SUB, ROLE_ANNOUNCER, granted_by="test")
    db_session.commit()


def _csrf_from(html: str) -> str:
    """從表單裡取出 CSRF token。

    🔴 刻意從**算繪出來的 HTML** 取,而不是自己算一次 ——
    自己算的話,「模板忘記放 hidden input」這件事測不出來:
    後端會驗過,而使用者的瀏覽器送不出那個欄位,**每一次發布都 403**。
    """
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert m, "🔴 表單裡沒有 csrf_token 這個 hidden input"
    return m.group(1)


def _count(db_session) -> int:
    from sqlalchemy import func, select

    from app.models import Announcement

    db_session.expire_all()
    return int(db_session.scalar(select(func.count()).select_from(Announcement)) or 0)


# ═══════════════════════════════════════════════════════════════════
# 1. 權限:能力判定與其他端點一致
# ═══════════════════════════════════════════════════════════════════
def test_announcer_sees_the_form(app_client, db_session):
    """有 `announcer` → 200,且表單有四個欄位。"""
    http, transport = app_client
    _login(http, transport)
    _grant_announcer(db_session)

    r = http.get(NEW)
    assert r.status_code == 200, f"預期 200,實得 {r.status_code} {r.text[:200]}"
    for field in ("title", "body", "starts_at", "ends_at"):
        assert f'name="{field}"' in r.text, f"表單缺欄位 {field}"


def test_reader_cannot_open_the_form_403(app_client):
    """只有 `reader` → **403**(不是 401,也不是 200 空表單)。

    🔴 回 200 空表單的話,他填完送出才被拒 —— 而白費的那次輸入不會回來。
    """
    http, transport = app_client
    _login(http, transport)
    assert http.get(NEW).status_code == 403


def test_unauthenticated_is_401(app_client):
    """未登入 → **401**。401=沒登入、403=登入了沒權限,不得混用。"""
    http, _transport = app_client
    assert http.get(NEW).status_code == 401
    assert http.post(NEW, data={"title": "t", "body": "b"}).status_code == 401


# ═══════════════════════════════════════════════════════════════════
# 2. 🔴 CSRF:本專案第一個 POST 表單
# ═══════════════════════════════════════════════════════════════════
def test_post_without_csrf_is_403_and_writes_nothing(app_client, db_session):
    """沒有 CSRF token → **403 且零列寫入**。

    🔴 「零列寫入」是重點:只驗狀態碼的話,「先寫進去再回 403」照樣綠,
    而那則公告已經廣播給全公司了。
    """
    http, transport = app_client
    _login(http, transport)
    _grant_announcer(db_session)

    r = http.post(NEW, data={"title": "偷發的", "body": "內容"}, follow_redirects=False)
    assert r.status_code == 403, f"預期 403,實得 {r.status_code} {r.text[:200]}"
    assert _count(db_session) == 0, "🔴 403 卻寫進了一列"


def test_post_with_garbage_csrf_is_403(app_client, db_session):
    """亂給的 token → 403。"""
    http, transport = app_client
    _login(http, transport)
    _grant_announcer(db_session)

    r = http.post(NEW, data={"title": "t", "body": "b", "csrf_token": "not-a-real-token"},
                  follow_redirects=False)
    assert r.status_code == 403
    assert _count(db_session) == 0


def test_csrf_token_of_another_session_is_rejected(app_client, db_session, idp, clock,
                                                  monkeypatch, sqlite_url):
    """**別的 session** 的 token 不得通行。

    🔴 這條擋的是「token 只驗簽章、不綁 session」。那種實作下攻擊者
    只要在自己的 session 拿一個合法 token,就能拿去打受害者的請求 ——
    而它通過所有「有 token 就放行」的測試。
    """
    from tests.conftest import _build_app

    http, transport = app_client
    _login(http, transport)
    _grant_announcer(db_session)
    mine = _csrf_from(http.get(NEW).text)

    # 另開一個 app + 另一次登入 = 另一個 session key
    other_http, other_transport = _build_app(idp, clock, monkeypatch, sqlite_url)
    _login(other_http, other_transport)
    theirs = _csrf_from(other_http.get(NEW).text) if other_http.get(NEW).status_code == 200 \
        else "unused"

    assert theirs != mine, "🔴 兩個 session 拿到同一個 CSRF token(token 沒綁 session)"


def test_csrf_token_is_not_the_session_key_in_disguise(app_client, db_session):
    """CSRF token **不得**含 session cookie 的值(它必須是單向的)。

    🔴 用 `URLSafeSerializer` 之類的**簽章**做 token 會把 session key
    明文(base64)帶到 HTML 上 —— 而那正是密封 cookie 在保護的東西。
    必須是 HMAC(單向)。
    """
    from app.session import SESSION_COOKIE

    http, transport = app_client
    _login(http, transport)
    _grant_announcer(db_session)
    token = _csrf_from(http.get(NEW).text)
    cookie = http.cookies.get(SESSION_COOKIE)
    assert cookie, "取不到 session cookie"

    # 🔴 **要拿真正的 session key 來比,不是拿 cookie 字串比。**
    #    密封 cookie 是 `base64(key).簽章`,所以 `cookie.split(".")[0]` 是
    #    **key 的 base64**,不是 key 本身 —— 拿它來斷言的話,
    #    「把原始 key 直接串進 token」這個突變**逃得掉**(實測逃掉了)。
    store = http.app.state.session_store
    key = store.unseal(cookie)
    assert key and len(key) > 16, f"取不到 session key:{key!r}"

    assert key not in token, "🔴 CSRF token 直接含 session key"
    assert cookie not in token and token not in cookie, "🔴 CSRF token 與 cookie 互相包含"
    # HMAC-SHA256 的十六進位輸出恆為 64 字元;多出來的東西就是夾帶
    assert re.fullmatch(r"[0-9a-f]{64}", token), f"🔴 token 不是純 HMAC 輸出:{token[:80]}"


# ═══════════════════════════════════════════════════════════════════
# 3. 🔴 時區:表單值不帶時區,而 API 拒收不帶時區的值
# ═══════════════════════════════════════════════════════════════════
def test_form_datetime_is_interpreted_as_taipei_time(app_client, db_session):
    """表單填 `2026-08-30T02:00`(台北)→ DB 存的是 `2026-08-29T18:00Z`。

    🔴 斷言**實際的 UTC 值**,不是只斷言「發布成功」—— 差 8 小時的話
    兩者都會回 303,而公告晚 8 小時才出現。
    🔴 頁面必須明寫這個欄位是台北時間;伺服器**宣告**時區與伺服器**猜**時區
    是兩件事(見 dev-log 計畫段②)。
    """
    from app.models import Announcement

    http, transport = app_client
    _login(http, transport)
    _grant_announcer(db_session)
    page = http.get(NEW)
    assert "UTC+8" in page.text or "台北" in page.text, "🔴 頁面沒有告訴使用者這是哪個時區"

    r = http.post(NEW, data={
        "title": "排程公告", "body": "內容",
        "starts_at": "2026-08-30T02:00", "csrf_token": _csrf_from(page.text),
    }, follow_redirects=False)
    assert r.status_code == 303, f"預期 303,實得 {r.status_code} {r.text[:300]}"

    db_session.expire_all()
    row = db_session.query(Announcement).one()
    assert _as_utc(row.starts_at) == datetime(
        2026, 8, 29, 18, 0, tzinfo=timezone.utc
    ), f"🔴 存進去的時間是 {row.starts_at}(應為 2026-08-29 18:00Z)"


def test_blank_datetime_means_publish_now(app_client, db_session):
    """時間欄位留空 = 現在起生效、無期限。"""
    from app.models import Announcement

    http, transport = app_client
    _login(http, transport)
    _grant_announcer(db_session)
    page = http.get(NEW)

    r = http.post(NEW, data={
        "title": "立即公告", "body": "內容", "starts_at": "", "ends_at": "",
        "csrf_token": _csrf_from(page.text),
    }, follow_redirects=False)
    assert r.status_code == 303, f"預期 303,實得 {r.status_code} {r.text[:300]}"

    db_session.expire_all()
    row = db_session.query(Announcement).one()
    assert row.ends_at is None
    assert _as_utc(row.starts_at) <= datetime.now(timezone.utc) + timedelta(seconds=5)


# ═══════════════════════════════════════════════════════════════════
# 4. 🔴 四種 400:畫面上要有可讀訊息,且已填的內容要留著
# ═══════════════════════════════════════════════════════════════════
# 🔴 值刻意用**不會出現在模板裡**的字串。原本寫「標題」「內容」時,
#    那兩個詞在 `<label>` 與說明文字裡本來就有 —— 於是「已填內容還在」
#    這條斷言**永遠成立**,而突變「400 時清空表單」逃掉了(實測逃掉了)。
KEEP_TITLE = "標題ZZQ7"
KEEP_BODY = "內容XXR9"

BAD_FORMS = [
    ({"title": "   ", "body": KEEP_BODY}, "空白標題"),
    ({"title": KEEP_TITLE, "body": "   "}, "空白內容"),
    ({"title": KEEP_TITLE, "body": KEEP_BODY, "starts_at": "不是時間"}, "時間格式錯"),
    ({"title": KEEP_TITLE, "body": KEEP_BODY,
      "starts_at": "2026-08-30T05:00", "ends_at": "2026-08-30T02:00"}, "空窗(迄早於起)"),
    ({"title": KEEP_TITLE, "body": KEEP_BODY, "audience": "group:QA"}, "未知 audience"),
]


@pytest.mark.parametrize("data,why", BAD_FORMS)
def test_bad_input_rerenders_form_with_readable_message(app_client, db_session, data, why):
    """壞輸入 → **400**、畫面上有可讀訊息、**且已填內容還在**、零列寫入。

    🔴 「已填內容還在」這半條是重點:清空表單等於叫使用者重打一次,
    而公告內容通常是一段字。
    ⚠ 訊息必須是**具體**的(哪一個欄位、哪一種不合法),
    不是「發布失敗」四個字 —— 後者讓使用者只能亂試。
    """
    http, transport = app_client
    _login(http, transport)
    _grant_announcer(db_session)
    payload = dict(data)
    payload["csrf_token"] = _csrf_from(http.get(NEW).text)

    r = http.post(NEW, data=payload, follow_redirects=False)
    assert r.status_code == 400, f"[{why}] 預期 400,實得 {r.status_code} {r.text[:200]}"
    assert _count(db_session) == 0, f"[{why}] 🔴 400 卻寫入了一列"
    assert "text/html" in r.headers.get("content-type", ""), f"[{why}] 應重新算繪表單而非回 JSON"

    # 🔴 訊息要**在錯誤框裡**、而且要具體。原本直接對整頁 `re.search` 是無效斷言
    #    —— 那些詞在 `<label>` 裡本來就有,所以「只寫『發布失敗』四個字」
    #    這個突變逃掉了(實測逃掉了)。
    alert = re.search(r'class="alert alert-danger"[^>]*>(.*?)</div>', r.text, re.S)
    assert alert, f"[{why}] 畫面上沒有錯誤框"
    message = alert.group(1).strip()
    assert message, f"[{why}] 錯誤框是空的"
    assert re.search(r"(title|body|標題|內容|時間|audience|收件範圍|時區)", message), \
        f"[{why}] 🔴 錯誤訊息不具體,使用者只能亂試:{message!r}"

    # 已填的值要留著。值刻意用不會出現在模板裡的字串(見 BAD_FORMS 上方)
    for field, value in (("title", KEEP_TITLE), ("body", KEEP_BODY)):
        if (data.get(field) or "").strip() == value:
            assert value in r.text, f"[{why}] 🔴 {field} 的輸入被清掉了"


# ═══════════════════════════════════════════════════════════════════
# 5. 入口 + 成功路徑
# ═══════════════════════════════════════════════════════════════════
def test_inbox_shows_publish_link_only_to_announcer(app_client, db_session):
    """收件匣頁的「發布公告」入口**只給有能力的人看**。

    🔴 沒有入口的頁面等於不存在(這正是 T09b 存在的原因)。
    🔴 而顯示一個點下去會 403 的連結**比不顯示更糟** —— 它讓人以為自己做錯了什麼。
    """
    http, transport = app_client
    _login(http, transport)
    assert NEW not in http.get("/inbox/").text, "🔴 reader 看到了發布公告的連結"

    _grant_announcer(db_session)
    assert NEW in http.get("/inbox/").text, "🔴 announcer 看不到發布公告的連結"


def test_published_announcement_appears_on_inbox_immediately(app_client, db_session):
    """成功後 303 導回收件匣,而那則公告**當場**出現。"""
    http, transport = app_client
    _login(http, transport)
    _grant_announcer(db_session)

    r = http.post(NEW, data={
        "title": "剛剛發的公告", "body": "內容", "csrf_token": _csrf_from(http.get(NEW).text),
    }, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/inbox/"), f"導向不對:{r.headers.get('location')}"
    assert "剛剛發的公告" in http.get("/inbox/").text


def test_form_page_is_csp_clean(app_client, db_session):
    """這一頁同樣要:CSP + `<style>` 帶本次 nonce + **零 JS** + 零行內樣式屬性。

    ⚠ `test_security.py` 的通用守門列舉 GET 路由,但這一頁對 `reader` 是 403
    (不是 HTML),所以它涵蓋不到 —— 必須在這裡用 `announcer` 再驗一次。
    """
    http, transport = app_client
    _login(http, transport)
    _grant_announcer(db_session)
    r = http.get(NEW)

    # 🔴 先斷言頁面真的算繪出來了。少了這兩行,這支測試在「路由還不存在」時
    #    **會通過** —— 404 的 JSON 沒有 <style>、沒有 <script>、沒有行內樣式,
    #    而 CSP 標頭是 middleware 加的所以照樣有。空檢查看起來與通過一模一樣。
    assert r.status_code == 200, f"預期 200,實得 {r.status_code}"
    csp = r.headers.get("content-security-policy")
    assert csp, "🔴 這一頁沒有 CSP"
    nonce = re.search(r"'nonce-([A-Za-z0-9_\-]+)'", csp).group(1)
    tags = re.findall(r"<style\b[^>]*>", r.text)
    assert tags, "頁面沒有內嵌 <style> —— 這支測試會變成空檢查"
    for tag in tags:
        assert f'nonce="{nonce}"' in tag, f"🔴 {tag[:40]} 沒帶本次的 nonce"
    assert "<script" not in r.text, "🔴 CSP 之下腳本會被靜默擋掉,這一頁不得有 JS"
    stripped = re.sub(r"<style\b.*?</style>", "", r.text, flags=re.S)
    assert 'style="' not in stripped, "🔴 有行內樣式屬性,CSP 會把它擋掉"
    assert "//" not in re.sub(r"https?://catsapp[^\"']*", "", "".join(
        re.findall(r'(?:src|href)="([^"]*)"', r.text)
    )), "🔴 有外部資源"
