# -*- coding: utf-8 -*-
"""T10 安全紅線紅測試(跳脫 / action_url 白名單 / 內容不進 log / 嚴格 CSP)。

對應驗收:`docs/任務表.md` T10、A.3〈本專案自我加嚴〉四條、契約 §4.10、
`docs/dev-logs/2026-08-25_T10_安全紅線落地.md` 的六條。

🔴 四類失敗,每一類都不會有錯誤訊息:

| 紅線 | 壞掉時的症狀 |
|---|---|
| 輸出跳脫 | stored XSS,而**同源之下一次 XSS 可觸及 IdP**;頁面看起來完全正常 |
| `action_url` 白名單 | 站內通知天生長得「可信」,是現成的釣魚載具 |
| 內容不進 log | 個資落在 log 裡,而**沒有任何人會發現** |
| CSP nonce 對不上 | 「整頁沒有樣式而伺服器零錯誤」——與 portal 2026-08-03 那次症狀完全相同 |
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import _login

SELF_SUB = "11111111-2222-3333-4444-555555555555"
XSS = "<script>alert(1)</script>"
SITE = "https://catsapp.sporton.com.tw"

HTML_PAGES = ("/inbox/", "/inbox/pending", "/inbox/logged-out/")


def _insert_message(db_session, **kw):
    """直接塞一列 `message` 進資料庫,**繞過所有應用層驗證**。

    回傳: Message
    副作用: INSERT 一列
    🔴 刻意繞過:要測「輸出側也擋」就必須造出寫入側會拒收的資料
    ——舊資料、手動修過的資料、被入侵寫進去的資料都長這樣。
    """
    from app.models import CATEGORY_SYSTEM, Message

    row = Message(
        id=uuid.uuid4(),
        recipient_sub=kw.pop("recipient", SELF_SUB),
        sender_sub=None,
        category=CATEGORY_SYSTEM,
        subject=kw.pop("subject", "主旨"),
        body=kw.pop("body", "內容"),
        source_app=kw.pop("source_app", "workorder"),
        is_read=False,
        created_at=datetime.now(timezone.utc),
        **kw,
    )
    db_session.add(row)
    db_session.flush()
    db_session.commit()
    return row


def _insert_announcement(db_session, *, title="公告", body="公告內容"):
    from app.models import AUDIENCE_ALL, Announcement

    row = Announcement(
        id=uuid.uuid4(), author_sub=SELF_SUB, title=title, body=body,
        audience=AUDIENCE_ALL, starts_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db_session.add(row)
    db_session.flush()
    db_session.commit()
    return row


# ═══════════════════════════════════════════════════════════════════
# 1. 🔴 輸出跳脫:stored XSS 是本系統第一風險
# ═══════════════════════════════════════════════════════════════════
def test_message_subject_and_body_are_escaped_on_page(app_client, db_session):
    """訊息的主旨與內容含 `<script>` 時,頁面必須輸出**跳脫後的字面文字**。

    🔴 同源之下(D2″ 單一 hostname)一次 stored XSS 可觸及 IdP。
    ⚠ 斷言方式:①原始的 `<script>` 標籤不得出現;②跳脫後的字面必須出現
    ——只驗①的話,「乾脆不顯示 body」也會通過,而那是功能壞掉不是安全達成。
    """
    http, transport = app_client
    _login(http, transport)
    _insert_message(db_session, subject=f"主旨{XSS}", body=f"內容{XSS}")

    r = http.get("/inbox/")
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in r.text, "🔴 未跳脫的 script 標籤出現在頁面上"
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in r.text, "跳脫後的字面沒有出現(body 根本沒顯示?)"


def test_announcement_title_and_body_are_escaped_on_page(app_client, db_session):
    """公告的標題與內容同樣必須跳脫。

    ⚠ 公告是**一則對多人**,所以一次注入的影響半徑比單封訊息大。
    """
    http, transport = app_client
    _login(http, transport)
    _insert_announcement(db_session, title=f"公告{XSS}", body=f"內文{XSS}")

    r = http.get("/inbox/")
    assert "<script>alert(1)</script>" not in r.text, "🔴 公告區塊未跳脫"
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in r.text


def test_admin_page_escapes_display_name(app_client, db_session):
    """角色後台的顯示名稱也要跳脫。

    🔴 這一頁是**唯一**會顯示姓名的地方(§4.2a L1),而那個值來自 IdP
    ——不是我方產生的,所以一樣不可信。
    """
    from app.authz import ROLE_ADMIN
    from app.models import AppUser
    from app.repo import grant_role

    http, transport = app_client
    _login(http, transport)
    db_session.expire_all()
    db_session.get(AppUser, SELF_SUB).display_name = f"陳{XSS}"
    grant_role(db_session, SELF_SUB, ROLE_ADMIN, granted_by="test")
    db_session.commit()

    r = http.get("/inbox/admin/users")
    assert r.status_code == 200, f"預期 200,實得 {r.status_code}"
    assert "<script>alert(1)</script>" not in r.text, "🔴 後台的顯示名稱未跳脫"


# ═══════════════════════════════════════════════════════════════════
# 2. 🔴 action_url 同站白名單:站內通知是現成的釣魚載具
# ═══════════════════════════════════════════════════════════════════
GOOD_URLS = [
    "/inbox/",
    "/compliance/trf/123",
    "/plm/case?id=42#tab",
    SITE + "/plm/",
    SITE,                                   # 站台本身(無尾斜線)
    "HTTPS://CATSAPP.SPORTON.COM.TW/plm/",  # scheme 與 host 大小寫不敏感
]

BAD_URLS = [
    ("https://evil.tld/",                  "外部網址"),
    ("http://catsapp.sporton.com.tw/x",    "降級成 http"),
    ("javascript:alert(1)",                "javascript: 協定"),
    ("JaVaScRiPt:alert(1)",                "javascript: 大小寫變形"),
    ("data:text/html,<script>alert(1)</script>", "data: 協定"),
    ("//evil.tld/x",                       "🔴 協定相對(//)"),
    ("/\\evil.tld/x",                      "🔴 協定相對的另一種寫法(/\\)——瀏覽器會正規化成 //"),
    (SITE + ".evil.tld/x",                 "🔴 前綴冒充:startswith(站台) 會通過而它是別人的網域"),
    (SITE + "@evil.tld/x",                 "🔴 userinfo 冒充"),
    (SITE + "\\@evil.tld/x",               "🔴 反斜線 + userinfo"),
    ("catsapp.sporton.com.tw/x",           "無 scheme(瀏覽器當相對路徑,但語意不明確)"),
    ("/x\x00/y",                           "含 NUL 控制字元"),
    ("/x\nhttps://evil.tld",               "含換行(瀏覽器會把 URL 裡的換行剝掉)"),
    ("/" + "a" * 600,                      "超長(超過欄寬 512)"),
]


@pytest.mark.parametrize("url", GOOD_URLS)
def test_action_url_accepts_same_site(url):
    """同站的值必須通過。

    ⚠ 這一組同樣重要:白名單太嚴會讓真的通知**發不出去**,
    而發不出去的症狀是 400 —— 那至少看得見。但把能用的擋掉仍然是 bug。
    """
    from app.validation import validate_action_url

    assert validate_action_url(url) is not None, f"同站值被拒收:{url}"


@pytest.mark.parametrize("url,why", BAD_URLS)
def test_action_url_rejects_counterexamples(url, why):
    """十四則反例一律 400。

    🔴 其中三則是「看起來會過」的經典洞:
      - `//evil.tld` 與 `/\\evil.tld`:都是**協定相對**,只擋 `//` 不夠;
      - `SITE + ".evil.tld"`:通過 `startswith(SITE)` 而網域是別人的
        —— 所以前綴之後**下一個字元必須是 `/` 或字串結束**。
    """
    from app.validation import BadRequest, validate_action_url

    with pytest.raises(BadRequest):
        validate_action_url(url)
    assert why  # 讓失敗訊息帶上「這是哪一則反例」


def test_action_url_none_and_blank_are_allowed_as_absent():
    """沒有 `action_url` 是合法的(不是每則通知都有可去的地方)。"""
    from app.validation import validate_action_url

    assert validate_action_url(None) is None
    assert validate_action_url("   ") is None


def test_message_write_path_validates_action_url(db_session):
    """`repo.create_message()` 對壞的 `action_url` 一律拒收。

    🔴 關卡設在**寫入路徑**而不是端點上,因為 `action_url` 的唯一寫入端點
    (推送 API)是 **T14** 才有的 —— 設在端點上等於在一個還不存在的地方
    設關卡,而 T14 動工時那是「附帶工作」,附帶工作最容易被忘記。
    """
    from app.repo import create_message
    from app.validation import BadRequest

    with pytest.raises(BadRequest):
        create_message(
            db_session, recipient_sub=SELF_SUB, subject="主旨", body="內容",
            action_url="https://evil.tld/", source_app="workorder",
        )
    from sqlalchemy import func, select

    from app.models import Message

    n = db_session.scalar(select(func.count()).select_from(Message))
    assert n == 0, "🔴 400 卻仍寫入了一列"


def test_message_is_only_constructed_in_repo_create_message():
    """源碼層檢查:`app/` 底下只有 `repo.create_message()` 可以構造 `Message`。

    🔴 **行為測試只能證明「這條路徑現在有驗」;源碼檢查證明「沒有第二條路徑」。**
    多開一條寫入路徑不會有錯誤訊息,只會讓「`action_url` 一律驗過」變成一句話。
    比照 `test_authz.py::test_display_name_only_written_from_own_login_token`。
    """
    import ast

    from tests.conftest import ROOT

    offenders = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id != "Message":
                continue
            # 允許的唯一位置:repo.py 的 create_message
            enclosing = [
                n.name for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef)
                and node.lineno >= n.lineno
                and node.lineno <= (n.end_lineno or n.lineno)
            ]
            if path.name == "repo.py" and "create_message" in enclosing:
                continue
            offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, f"🔴 `Message` 在 repo.create_message 之外被構造:{offenders}"


def test_bad_action_url_already_in_db_is_not_rendered(app_client, db_session):
    """**輸出側也擋**:資料庫裡已經有壞的 `action_url` 時不得輸出成連結。

    🔴 寫入側的驗證是 T10 才加的,而在它之前寫進去的列、手動改過的列、
    被入侵寫進去的列都繞過了它。**輸出側再擋一次**是唯一能保護既有資料的做法。
    ⚠ 這裡刻意用 `_insert_message` 繞過應用層 —— 用 API 造不出這種資料。
    """
    http, transport = app_client
    _login(http, transport)
    _insert_message(db_session, subject="看似正常", action_url="javascript:alert(1)")

    page = http.get("/inbox/")
    api = http.get("/inbox/api/v1/messages")
    assert "javascript:alert(1)" not in page.text, "🔴 javascript: 連結被算繪到頁面上"
    assert "javascript:alert(1)" not in api.text, "🔴 javascript: 連結出現在 API 回應裡"
    assert api.json()["items"][0]["action_url"] is None, "壞的 action_url 應輸出成 None"


def test_good_action_url_still_renders(app_client, db_session):
    """把守門調成「全部丟掉」不算通過:合法的 `action_url` 必須還在。

    ⚠ 沒有這一條的話,一個「永遠回 None」的實作會讓上面那支測試變綠,
    而「前往處理」那顆按鈕從此不會出現 —— **而那不會有錯誤訊息**。
    """
    http, transport = app_client
    _login(http, transport)
    _insert_message(db_session, subject="有連結", action_url="/compliance/trf/1")

    r = http.get("/inbox/api/v1/messages")
    assert r.json()["items"][0]["action_url"] == "/compliance/trf/1"
    assert "/compliance/trf/1" in http.get("/inbox/").text


# ═══════════════════════════════════════════════════════════════════
# 3. 🔴 內容不進 log
# ═══════════════════════════════════════════════════════════════════
def test_subject_and_body_never_appear_in_logs(app_client, db_session, capsys):
    """走完全流程,stdout 不得出現任何主旨 / 內容 / 公告標題。

    🔴 A.3:log 只記 id、`sub`、事件類型。個資落進 log 之後
    **沒有任何人會發現** —— log 不會有人逐行讀,而它會被備份、被轉送。
    """
    from app.authz import ROLE_ANNOUNCER
    from app.repo import grant_role

    http, transport = app_client
    _login(http, transport)
    grant_role(db_session, SELF_SUB, ROLE_ANNOUNCER, granted_by="test")
    db_session.commit()

    secret_subject = "機密主旨ZZTOP"
    secret_body = "機密內容QQPLM"
    secret_title = "機密公告XXKEY"
    msg = _insert_message(db_session, subject=secret_subject, body=secret_body)

    capsys.readouterr()          # 丟掉登入階段的輸出,只看以下這幾步
    http.get("/inbox/api/v1/messages")
    http.post(f"/inbox/api/v1/messages/{msg.id}/read")
    http.post("/inbox/api/v1/announcements",
              json={"title": secret_title, "body": "公告內容"})
    http.get("/inbox/api/v1/announcements/active")
    http.get("/inbox/")
    out = capsys.readouterr().out

    for secret in (secret_subject, secret_body, secret_title):
        assert secret not in out, f"🔴 log 出現了內容:{secret}"
    assert out.strip(), "沒有抓到任何 log —— 這支測試會變成永遠通過的空檢查"


def test_every_log_line_is_single_line_json(app_client, db_session, capsys):
    """共通紅線:log 走 stdout、**單行 JSON**。

    ⚠ 多行的 log 在集中蒐集時會被切成幾筆看不懂的東西,而**當下不會有錯誤**
    ——是事後查問題的人才會撞到。
    """
    http, transport = app_client
    capsys.readouterr()
    _login(http, transport)
    msg = _insert_message(db_session)
    http.post(f"/inbox/api/v1/messages/{msg.id}/read")
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]

    assert lines, "沒有抓到任何 log"
    for line in lines:
        parsed = json.loads(line)          # 不是 JSON 就會在這裡爆
        assert "event" in parsed, f"log 缺 event 欄位:{line}"


# ═══════════════════════════════════════════════════════════════════
# 4. 🔴 嚴格 CSP + 逐請求 nonce(契約 §4.10)
# ═══════════════════════════════════════════════════════════════════
def _csp_of(response) -> str:
    csp = response.headers.get("content-security-policy")
    assert csp, "🔴 回應沒有 Content-Security-Policy 標頭"
    return csp


@pytest.mark.parametrize("path", HTML_PAGES)
def test_html_pages_have_strict_csp(app_client, path):
    """每個 HTML 頁都要有嚴格 CSP,且不得含三種等於沒設的值。"""
    http, transport = app_client
    _login(http, transport)
    csp = _csp_of(http.get(path))

    assert "default-src 'none'" in csp, f"CSP 缺 default-src 'none':{csp}"
    assert "frame-ancestors 'none'" in csp, "CSP 缺 frame-ancestors(點擊劫持)"
    for forbidden in ("unsafe-inline", "unsafe-eval"):
        assert forbidden not in csp, f"🔴 CSP 含 {forbidden},等於沒設:{csp}"
    assert " *" not in csp and "*;" not in csp, f"🔴 CSP 含萬用字元:{csp}"


def test_every_html_response_has_csp(app_client):
    """**列舉所有 GET 路由**,凡回 `text/html` 者一律要有 CSP。

    🔴 這條的用意是**涵蓋未來的新頁面**。逐路由加標頭的話,下一個新頁面
    會忘記,而忘記不會有任何症狀 —— 所以 CSP 由 middleware 統一加,
    而這支測試證明「統一」是真的。
    """
    http, transport = app_client
    _login(http, transport)
    checked = 0
    for route in http.app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if "GET" not in methods or "{" in path or "/assets" in path:
            continue
        r = http.get(path, follow_redirects=False)
        if "text/html" not in r.headers.get("content-type", ""):
            continue
        checked += 1
        assert r.headers.get("content-security-policy"), f"🔴 {path} 沒有 CSP"
    assert checked >= 3, f"應該至少檢查到 3 個 HTML 路由,實際 {checked}"


@pytest.mark.parametrize("path", HTML_PAGES)
def test_inline_style_carries_the_nonce_from_this_response(app_client, path):
    """頁內每個 `<style` / `<script` 都必須帶**這一次回應標頭裡的**那個 nonce。

    🔴 對不上的症狀是「**整頁沒有樣式而伺服器零錯誤**」——與 portal
    2026-08-03 踩的那次(檔案不在 mount 目錄下)症狀完全相同。
    ⚠ 所以不能只驗「標頭有 nonce」或「模板有 nonce 屬性」,
    必須驗**兩者是同一個值**。
    """
    http, transport = app_client
    _login(http, transport)
    r = http.get(path)
    csp = _csp_of(r)
    m = re.search(r"'nonce-([A-Za-z0-9_\-]+)'", csp)
    assert m, f"CSP 沒有 nonce:{csp}"
    nonce = m.group(1)

    tags = re.findall(r"<(?:style|script)\b[^>]*>", r.text)
    assert tags, f"{path} 沒有內嵌 style/script —— 這支測試會變成空檢查"
    for tag in tags:
        assert f'nonce="{nonce}"' in tag, f"🔴 {path} 的 {tag[:40]} 沒帶本次的 nonce"


def test_nonce_differs_between_requests(app_client):
    """兩次請求的 nonce 必須不同。

    🔴 寫死一個 nonce 等於**完全沒有 nonce**:攻擊者只要注入一次就永久有效。
    而寫死的版本在畫面上與正確的版本**一模一樣**。
    """
    http, transport = app_client
    _login(http, transport)
    a = _csp_of(http.get("/inbox/"))
    b = _csp_of(http.get("/inbox/"))
    assert a != b, "🔴 兩次請求的 CSP(含 nonce)完全相同 —— nonce 是寫死的"


def test_local_stylesheet_is_still_allowed_and_served(app_client):
    """本地託管的 Bootstrap 必須仍然載得到(CSP 要允許 `'self'` 的樣式)。

    ⚠ 沒有這一條的話,一個「CSP 嚴到把自家 CSS 也擋掉」的設定會讓上面
    每一支 CSP 測試都綠,而**整頁沒有樣式**。
    """
    http, transport = app_client
    _login(http, transport)
    csp = _csp_of(http.get("/inbox/"))
    assert re.search(r"style-src[^;]*'self'", csp), f"CSP 的 style-src 未允許 'self':{csp}"
    assert http.get("/inbox/assets/vendor/bootstrap.min.css").status_code == 200


def test_security_headers_present(app_client):
    """順帶釘住兩個標頭:`nosniff` 與 `Referrer-Policy`。

    ⚠ 兩者都不是 CSP 的一部分,但缺了同樣沒有症狀。
    """
    http, transport = app_client
    _login(http, transport)
    h = http.get("/inbox/").headers
    assert h.get("x-content-type-options") == "nosniff"
    assert h.get("referrer-policy"), "缺 Referrer-Policy"


def test_rendered_pages_have_no_inline_style_attributes(app_client, db_session):
    """算繪出來的頁面**不得有行內樣式屬性**。

    🔴 這是 T10 動工時才發現的:**CSP 的 nonce 只對「元素」有效,對「屬性」無效。**
       `style-src 'nonce-…'`(不含 `'unsafe-inline'`)會把 `<div style="…">`
       整批擋掉 —— 而症狀是**版面走鐘、伺服器零錯誤**,與 portal 2026-08-03
       那次(檔案不在 mount 目錄下 → 整頁沒樣式)是同一種。
    ⚠ 所以「`<style>` 帶了 nonce」不等於「樣式會生效」。要加樣式一律加 class。
    ⚠ 驗**算繪後**而不是驗模板原始碼:模板裡的註解會提到這個屬性名,
      而註解不會出現在瀏覽器看到的東西裡。
    """
    from app.authz import ROLE_ADMIN
    from app.repo import grant_role

    http, transport = app_client
    _login(http, transport)
    grant_role(db_session, SELF_SUB, ROLE_ADMIN, granted_by="test")
    db_session.commit()
    _insert_message(db_session, action_url="/compliance/trf/1")
    _insert_announcement(db_session)

    for path in (*HTML_PAGES, "/inbox/admin/users"):
        html = http.get(path).text
        # 拿掉 <style> 區塊本身(它是合法的、帶 nonce 的元素)
        stripped = re.sub(r"<style\b.*?</style>", "", html, flags=re.S)
        assert 'style="' not in stripped, f"🔴 {path} 有行內樣式屬性,CSP 會把它擋掉"
