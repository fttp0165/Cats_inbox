# -*- coding: utf-8 -*-
"""T08b:五個 HTML 頁面統一載入本地 Bootstrap 5。

🔴 這一組擋的是三種**沒有錯誤訊息**的退化:
  1. 某一頁用手寫 CSS 而非共用的 Bootstrap —— 看起來「只是比較醜」,而手機寬會橫向捲動
     (2026-10-08 檢視:角色後台在 390px 寬 scrollWidth 524);
  2. CSS 路徑寫死 `/inbox/assets/…` 而不是 `{{ base_path }}` —— base_path 一改,那一頁就變成無樣式,
     伺服器仍回 200;
  3. 把 Markdown 的 `**` 寫進模板 —— 畫面上原樣出現星號(2026-10-08 待開通頁實際發生;
     與 portal 的 PA-23 同型)。

⚠ 刻意用**算繪後的頁面**驗(不是 grep 模板原始碼):守門的對象是使用者看到的東西。
"""
import re
from pathlib import Path

import pytest

from tests.conftest import _login

SUB = "11111111-2222-3333-4444-555555555555"
TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"
BOOTSTRAP_LINK = '<link rel="stylesheet" href="/inbox/assets/vendor/bootstrap.min.css">'


def _login_full(app_client_bootstrap, db_session):
    """登入成 bootstrap 管理員,再補 announcer,讓五頁都開得了。"""
    http, transport = app_client_bootstrap
    _login(http, transport)
    from app import repo

    repo.grant_role(db_session, SUB, "announcer", granted_by=SUB)
    db_session.commit()
    return http


PAGES = ["/inbox/", "/inbox/announcements/new", "/inbox/admin/users", "/inbox/pending"]


@pytest.mark.parametrize("path", PAGES)
def test_every_page_links_local_bootstrap(app_client_bootstrap, db_session, path):
    http = _login_full(app_client_bootstrap, db_session)
    r = http.get(path)
    assert r.status_code == 200, (path, r.status_code)
    assert BOOTSTRAP_LINK in r.text, f"{path} 沒有載入本地 Bootstrap(§4.10 禁 CDN,樣式只能來自同源)"
    assert "cdn." not in r.text and "https://" not in re.sub(r'href="https://catsapp\.sporton\.com\.tw[^"]*"', "", r.text), \
        f"{path} 出現外部資源連結(契約 §4.10 禁外部 CDN)"


def test_logged_out_page_links_local_bootstrap(app_client_bootstrap):
    http, _ = app_client_bootstrap
    r = http.get("/inbox/logged-out/")
    assert r.status_code == 200
    assert BOOTSTRAP_LINK in r.text, "已登出頁沒有載入本地 Bootstrap"


def test_templates_use_base_path_for_assets():
    """模板裡的資產路徑一律 `{{ base_path }}/assets/…`,不得寫死 `/inbox/assets/`。"""
    bad = []
    for t in sorted(TEMPLATES.glob("*.html")):
        src = t.read_text(encoding="utf-8")
        if "/inbox/assets/" in src:
            bad.append(t.name)
        if "/assets/vendor/bootstrap.min.css" not in src:
            bad.append(f"{t.name}(沒有 Bootstrap link)")
    assert not bad, f"資產路徑寫死或缺 Bootstrap:{bad}"


@pytest.mark.parametrize("path", PAGES + ["/inbox/logged-out/"])
def test_no_literal_markdown_asterisks_on_pages(app_client_bootstrap, db_session, path):
    """算繪後的頁面不得出現 `**`(Markdown 強調寫進模板會原樣印出來)。"""
    http = _login_full(app_client_bootstrap, db_session)
    r = http.get(path)
    assert r.status_code == 200
    # 只看 body(head 的 <style> 註解不算畫面)
    body = r.text.split("<body", 1)[1]
    assert "**" not in body, f"{path} 畫面上出現字面 `**`"


def test_admin_table_is_responsive(app_client_bootstrap, db_session):
    """角色後台的表格包在 `.table-responsive` 裡:手機寬不橫向捲動整頁,只捲表格。"""
    http = _login_full(app_client_bootstrap, db_session)
    r = http.get("/inbox/admin/users")
    assert r.status_code == 200
    assert 'class="table-responsive"' in r.text, "角色後台表格沒有 .table-responsive(390px 寬整頁橫向捲動)"
    assert "table table-sm" in r.text, "角色後台表格沒有用 Bootstrap 的 .table"
    assert f'href="/inbox/"' in r.text, "角色後台沒有「回收件匣」的連結"
