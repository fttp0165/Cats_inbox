# -*- coding: utf-8 -*-
"""T08d:收件匣「標為已讀」表單 + 瀏覽器錯誤頁。

🔴 擋兩種沒有錯誤訊息的缺口:
  1. 未讀徽章從畫面上永遠消不掉(標已讀只有 JSON API,零 JS 的頁面按不到);
  2. 瀏覽器使用者看到 `{"error":"forbidden"}` 這種裸 JSON(2026-10-08 真人冒煙實際發生)。
⚠ 錯誤頁**只換 body 不換狀態碼**,而且只對瀏覽器(Accept 含 text/html);
  非瀏覽器呼叫方仍拿 JSON —— 最後一支測試釘住。
"""
import re
import uuid

from tests.conftest import _login
from tests.test_security import SELF_SUB, _insert_announcement, _insert_message

HTML = {"Accept": "text/html,application/xhtml+xml"}


def _csrf(html: str) -> str:
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, "頁面上沒有 csrf_token"
    return m.group(1)


def _logged_in(app_client):
    http, transport = app_client
    _login(http, transport)
    return http


# ── 標為已讀 ──────────────────────────────────────────────────────────

def test_unread_message_has_mark_read_form(app_client, db_session):
    http = _logged_in(app_client)
    msg = _insert_message(db_session)
    html = http.get("/inbox/").text
    assert 'action="/inbox/read/message"' in html, "未讀訊息沒有「標為已讀」表單"
    assert f'name="message_id" value="{msg.id}"' in html
    assert 'action="/inbox/read/all"' in html, "頁首沒有「全部標為已讀」"


def test_mark_message_read_via_form(app_client, db_session):
    http = _logged_in(app_client)
    msg = _insert_message(db_session)
    token = _csrf(http.get("/inbox/").text)
    r = http.post("/inbox/read/message", data={"message_id": str(msg.id), "csrf_token": token},
                  follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/inbox/"
    db_session.expire_all()
    assert http.get("/inbox/api/v1/messages").json()["items"][0]["is_read"] is True
    assert f'name="message_id" value="{msg.id}"' not in http.get("/inbox/").text, "已讀後按鈕應消失"


def test_cannot_mark_someone_elses_message(app_client, db_session):
    http = _logged_in(app_client)
    other = _insert_message(db_session, recipient="99999999-9999-9999-9999-999999999999")
    _insert_message(db_session)  # 自己有一則未讀,頁面上才有表單可取 token
    token = _csrf(http.get("/inbox/").text)
    r = http.post("/inbox/read/message", data={"message_id": str(other.id), "csrf_token": token},
                  follow_redirects=False)
    assert r.status_code == 403, f"🔴 標別人的訊息應 403,得到 {r.status_code}"
    db_session.expire_all()
    db_session.refresh(other)
    assert other.is_read is False, "🔴 403 卻改了別人的訊息"


def test_malformed_id_is_400_not_403(app_client, db_session):
    """形狀不對是 400:403 會讓 CSRF 列舉守門以為「帶 token 也被拒」。"""
    http = _logged_in(app_client)
    _insert_message(db_session)
    token = _csrf(http.get("/inbox/").text)
    for path, field in (("/inbox/read/message", "message_id"), ("/inbox/read/announcement", "announcement_id")):
        r = http.post(path, data={field: "x", "csrf_token": token}, follow_redirects=False)
        assert r.status_code == 400, (path, r.status_code)


def test_mark_announcement_read_via_form(app_client, db_session):
    http = _logged_in(app_client)
    ann = _insert_announcement(db_session)
    html = http.get("/inbox/").text
    assert f'name="announcement_id" value="{ann.id}"' in html
    r = http.post("/inbox/read/announcement",
                  data={"announcement_id": str(ann.id), "csrf_token": _csrf(html)}, follow_redirects=False)
    assert r.status_code == 303
    assert f'name="announcement_id" value="{ann.id}"' not in http.get("/inbox/").text


def test_mark_all_read(app_client, db_session):
    http = _logged_in(app_client)
    _insert_message(db_session); _insert_message(db_session); _insert_announcement(db_session)
    token = _csrf(http.get("/inbox/").text)
    r = http.post("/inbox/read/all", data={"csrf_token": token}, follow_redirects=False)
    assert r.status_code == 303
    assert http.get("/inbox/api/v1/messages/unread-count").json()["unread"] == 0
    assert 'name="announcement_id"' not in http.get("/inbox/").text


def test_mark_read_requires_csrf(app_client, db_session):
    http = _logged_in(app_client)
    msg = _insert_message(db_session)
    r = http.post("/inbox/read/message", data={"message_id": str(msg.id)}, follow_redirects=False)
    assert r.status_code == 403


# ── 瀏覽器錯誤頁 ──────────────────────────────────────────────────────

def _is_error_page(r, status: int, text: str):
    assert r.status_code == status, (r.status_code, r.text[:200])
    assert "text/html" in r.headers["content-type"], f"瀏覽器拿到的不是 HTML:{r.headers['content-type']}"
    assert '<nav class="app-nav' in r.text, "錯誤頁沒有共用版面"
    assert '{"error"' not in r.text and text in r.text, r.text[:300]


def test_browser_401_page(app_client):
    http, _ = app_client
    r = http.get("/inbox/", headers=HTML)
    _is_error_page(r, 401, "請先登入")
    assert 'href="/inbox/oidc/login"' in r.text


def test_browser_403_page(app_client):
    http = _logged_in(app_client)  # 只有 reader
    r = http.get("/inbox/admin/users", headers=HTML)
    _is_error_page(r, 403, "沒有這個功能的權限")
    assert 'href="/inbox/pending"' in r.text


def test_browser_404_page(app_client):
    http, _ = app_client
    _is_error_page(http.get("/inbox/no-such-page", headers=HTML), 404, "找不到")


def test_browser_csrf_page(app_client, db_session):
    http = _logged_in(app_client)
    msg = _insert_message(db_session)
    r = http.post("/inbox/read/message", data={"message_id": str(msg.id), "csrf_token": "bad"},
                  headers=HTML, follow_redirects=False)
    _is_error_page(r, 403, "頁面已過期")


def test_non_browser_and_api_still_get_json(app_client):
    """🔴 API 與非瀏覽器呼叫方的契約不變。"""
    http, _ = app_client
    assert http.get("/inbox/").json() == {"error": "not_authenticated"}
    r = http.get("/inbox/api/v1/messages", headers=HTML)
    assert r.status_code == 401 and r.json() == {"error": "not_authenticated"}, "API 路徑即使 Accept html 也要 JSON"
