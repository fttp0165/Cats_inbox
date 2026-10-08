# -*- coding: utf-8 -*-
"""T08c:介面視覺改版的守門(共用版面 + 導覽列 + 台北時間 + 空狀態)。

🔴 這一組擋的是四種**沒有錯誤訊息**的退化:
  1. 某一頁沒載共用的 `app.css` —— 那一頁「只是長得不一樣」,伺服器照樣 200;
  2. 導覽列不看能力 —— `reader` 看到「角色後台」,點下去 403,他以為自己做錯了什麼(T09b 原則);
  3. 時間退回 ISO UTC 含微秒(`2026-10-08T09:08:49.602715+00:00`)—— 使用者看不懂,
     而且與發布頁宣告的「台北時間(UTC+8)」差 8 小時;
  4. 新頁面自己寫一份 `<head>` 而不 extends 共用版面 —— 導覽列、CSP nonce、Bootstrap link 三件事
     從此要在兩個地方維護,遲早漂移(portal 反覆記過的「兩份必然漂移」)。

⚠ 頁面層的斷言一律用**算繪後的頁面**(守門的對象是使用者看到的東西);
  結構層(extends)才看模板原始碼。
"""
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.conftest import _login
from tests.test_security import _insert_announcement, _insert_message

SUB = "11111111-2222-3333-4444-555555555555"
TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"
APP_CSS_LINK = '<link rel="stylesheet" href="/inbox/assets/app.css">'
NAV_OPEN = '<nav class="app-nav'

SIGNED_IN_PAGES = ["/inbox/", "/inbox/announcements/new", "/inbox/admin/users"]
ALL_PAGES = SIGNED_IN_PAGES + ["/inbox/pending", "/inbox/logged-out/"]

# ISO UTC 含微秒的形狀 —— 畫面上**不得**出現
_RAW_ISO = re.compile(r"\d{2}:\d{2}:\d{2}\.\d+")
# 台北時間到分 —— 畫面上**必須**出現
_TAIPEI = re.compile(r"\b\d{4}-\d{2}-\d{2} \d{2}:\d{2}\b")


def _login_full(app_client_bootstrap, db_session):
    """登入成 bootstrap 管理員,再補 announcer,讓三個登入頁都開得了。"""
    http, transport = app_client_bootstrap
    _login(http, transport)
    from app import repo

    repo.grant_role(db_session, SUB, "announcer", granted_by=SUB)
    db_session.commit()
    return http


def _body(html: str) -> str:
    """只看 <body>:head 裡的 <style> 註解不是畫面。"""
    return html.split("<body", 1)[1]


# ── 1. 共用樣式 ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ALL_PAGES)
def test_every_page_links_shared_app_css(app_client_bootstrap, db_session, path):
    http = _login_full(app_client_bootstrap, db_session)
    r = http.get(path)
    assert r.status_code == 200, (path, r.status_code)
    assert APP_CSS_LINK in r.text, f"{path} 沒有載入共用的 app.css(那一頁會長得跟其他頁不一樣,伺服器零錯誤)"


def test_app_css_is_actually_served(app_client):
    """🔴 打實際的 URL —— 檔案在 repo 裡、路徑也對、而 app 不送(portal 2026-08-03)。"""
    http, _ = app_client
    r = http.get("/inbox/assets/app.css")
    assert r.status_code == 200, f"app.css 送不出來:{r.status_code}"
    assert "text/css" in r.headers.get("content-type", ""), r.headers.get("content-type")


# ── 2. 導覽列 ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", SIGNED_IN_PAGES)
def test_signed_in_pages_share_the_nav(app_client_bootstrap, db_session, path):
    http = _login_full(app_client_bootstrap, db_session)
    html = http.get(path).text
    assert NAV_OPEN in html, f"{path} 沒有共用的導覽列"
    nav = html.split(NAV_OPEN, 1)[1].split("</nav>", 1)[0]
    assert 'href="/inbox/"' in nav and "收件匣" in nav, f"{path} 導覽列缺「收件匣」"
    assert 'href="/"' in nav and "入口" in nav, f"{path} 導覽列缺回統一入口 `/` 的連結(T08b §六 第 4 項)"
    assert 'href="/inbox/logout"' in nav, f"{path} 導覽列缺「登出」"


def test_nav_shows_admin_and_publish_only_with_capability(app_client_bootstrap, db_session):
    http = _login_full(app_client_bootstrap, db_session)
    nav = http.get("/inbox/").text.split(NAV_OPEN, 1)[1].split("</nav>", 1)[0]
    assert 'href="/inbox/admin/users"' in nav, "admin 在導覽列看不到「角色後台」"
    assert 'href="/inbox/announcements/new"' in nav, "announcer 在導覽列看不到「發布公告」"


def test_nav_hides_admin_and_publish_from_reader(app_client):
    """🔴 `reader` 不得看到會 403 的連結 —— 顯示一個點下去會被拒的入口比不顯示更糟。"""
    http, transport = app_client
    _login(http, transport)
    html = http.get("/inbox/").text
    assert NAV_OPEN in html
    assert 'href="/inbox/admin/users"' not in html, "🔴 reader 看到了「角色後台」"
    assert 'href="/inbox/announcements/new"' not in html, "🔴 reader 看到了「發布公告」"


# ── 3. 時間顯示 ─────────────────────────────────────────────────────────────

def test_taipei_filter():
    from app.templating import taipei

    assert taipei("2026-10-08T09:08:49.602715+00:00") == "2026-10-08 17:08"
    assert taipei("2026-08-30T02:00:00+00:00") == "2026-08-30 10:00"
    assert taipei(datetime(2026, 10, 8, 9, 8, 49, tzinfo=timezone.utc)) == "2026-10-08 17:08"
    # naive 視為 UTC(與 `iso_utc` 同一個慣例:寫入端一律 UTC,naive 只在 SQLite 上出現)
    assert taipei(datetime(2026, 10, 8, 9, 8, 49)) == "2026-10-08 17:08"
    assert taipei(None) == "—"
    assert taipei("") == "—"


def test_inbox_shows_taipei_time_not_raw_iso(app_client, db_session):
    http, transport = app_client
    _login(http, transport)
    _insert_message(db_session)
    _insert_announcement(db_session)
    body = _body(http.get("/inbox/").text)
    assert "+00:00" not in body, "🔴 收件匣畫面上出現 `+00:00`(ISO UTC 直接印出來)"
    assert not _RAW_ISO.search(body), "🔴 收件匣畫面上出現含微秒的時間"
    assert _TAIPEI.search(body), "收件匣畫面上找不到 `YYYY-MM-DD HH:MM` 形狀的時間"


def test_api_still_returns_iso_utc(app_client, db_session):
    """🔴 畫面改了,API 不能跟著改:`iso_utc` 是輸入側拒收 naive 的對稱義務。"""
    http, transport = app_client
    _login(http, transport)
    _insert_message(db_session)
    item = http.get("/inbox/api/v1/messages").json()["items"][0]
    assert item["created_at"].endswith("+00:00"), item["created_at"]


def test_admin_page_shows_cache_time_in_taipei(app_client_bootstrap, db_session):
    http = _login_full(app_client_bootstrap, db_session)
    from app.models import AppUser

    user = db_session.query(AppUser).filter_by(sub=SUB).one()
    user.display_name = "測試"
    user.display_name_updated_at = datetime(2026, 10, 8, 1, 2, 3, 456789, tzinfo=timezone.utc)
    db_session.commit()
    body = _body(http.get("/inbox/admin/users").text)
    assert "2026-10-08 09:02" in body, "後台的「更新於」沒有轉成台北時間到分"
    assert "+00:00" not in body and not _RAW_ISO.search(body), "🔴 後台畫面上出現 ISO UTC"


# ── 4. 空狀態與結構 ─────────────────────────────────────────────────────────

def test_empty_inbox_has_an_empty_state(app_client):
    http, transport = app_client
    _login(http, transport)
    body = _body(http.get("/inbox/").text)
    assert 'class="empty-state' in body, "空收件匣沒有空狀態區塊"
    assert "<svg" in body, "空狀態沒有內嵌 SVG 插圖(零外部資源,所以只能內嵌)"


@pytest.mark.parametrize("path", ALL_PAGES)
def test_pages_have_no_script_tags(app_client_bootstrap, db_session, path):
    """CSP 沒有 `script-src`:任何 `<script>` 都會被**靜默**擋掉,所以模板裡出現它一定是錯的。"""
    http = _login_full(app_client_bootstrap, db_session)
    assert "<script" not in http.get(path).text, f"{path} 有 <script>(會被 CSP 靜默擋掉)"


def test_page_templates_extend_the_shared_layout():
    """頁面模板一律 `{% extends "layout/base.html" %}`:head / 導覽列 / nonce 只在一個地方維護。"""
    layout = TEMPLATES / "layout" / "base.html"
    assert layout.exists(), "缺共用版面 app/templates/layout/base.html"
    bad = [
        t.name for t in sorted(TEMPLATES.glob("*.html"))
        if '{% extends "layout/base.html" %}' not in t.read_text(encoding="utf-8")
    ]
    assert not bad, f"這些頁面模板沒有 extends 共用版面:{bad}"
