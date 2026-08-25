# -*- coding: utf-8 -*-
"""T10b CSRF 紅測試:**列舉式守門** + 兩個後台表單的零副作用。

對應驗收:`docs/任務表.md` T10b、`docs/dev-logs/2026-08-25_T10b_CSRF補齊與列舉式守門.md`。

🔴 **這個洞不是新破的,是從 T05 就一直開著而沒有人注意到。**
角色後台的兩個表單能讓攻擊者**替自己開通任何角色**、**整批清掉顯示名稱快取**
—— 而成功時只是一個 303 導回清單頁,**管理員看到的畫面完全正常**。

🔴 **守門必須是列舉式。** 逐支列出的話,下一個新表單不會被列進去,
而漏掉沒有任何症狀 —— 表單照樣送得出去,只是沒有保護。
與 `test_security.py::test_every_html_response_has_csp` 同一個形狀。
"""

from __future__ import annotations

import inspect
import re

from fastapi import params as fastapi_params

from tests.conftest import _login

SELF_SUB = "11111111-2222-3333-4444-555555555555"
ADMIN_FORMS = (
    "/inbox/admin/users/roles",
    "/inbox/admin/users/purge-display-names",
)


def _grant_all(db_session):
    """把**全部**角色給替身那個 sub。

    🔴 為什麼要全部:列舉式守門會打每一個表單路由,而 403 可能來自
       **沒有權限**而不是**沒有 CSRF** —— 兩者的回應都是 403。
       身上有全部角色時,任何 403 就只能是 CSRF 的那一個。
    """
    from app.authz import ALL_ROLES
    from app.repo import grant_role

    for role in ALL_ROLES:
        grant_role(db_session, SELF_SUB, role, granted_by="test")
    db_session.commit()


def _form_post_routes(app) -> list:
    """列舉「接受表單的 POST 路由」。

    回傳: [(path, [Form 欄位名, ...]), ...]
    副作用: 無

    判準是**這個路由收不收表單**,不是「它會不會寫資料」:
    🔴 `application/x-www-form-urlencoded` 是 CORS 的「簡單內容型別」,
       跨站表單**送得出去**;而 `application/json` 會觸發預檢並被擋掉。
       所以 JSON 端點不在 CSRF 的風險面上,表單端點才在。
    ⚠ 日後若有端點改成接受 `text/plain` 或 `multipart`,判準要跟著改。
    """
    from tests.conftest import iter_routes

    found = []
    for full_path, methods, endpoint in iter_routes(app):
        if "POST" not in methods or endpoint is None:
            continue
        fields = [
            name
            for name, p in inspect.signature(endpoint).parameters.items()
            if isinstance(p.default, fastapi_params.Form)
        ]
        if fields:
            found.append((full_path, fields))
    return found


def _dummy_payload(fields) -> dict:
    """給每個表單欄位一個能過型別的假值。

    ⚠ 值不必通過**業務**驗證 —— 400 與 403 是兩件事,而本檔只在意 403。
    只有 `role` 給真值,因為未知角色會在 CSRF 之後才被擋(那也只是 400)。
    """
    out = {}
    for name in fields:
        if name == "csrf_token":
            continue
        out[name] = {"role": "reader", "enabled": "0", "sub": SELF_SUB}.get(name, "x")
    return out


def _csrf_of(http) -> str:
    """從後台頁取出 CSRF token(那一頁一定是登入且有 admin 才看得到)。"""
    html = http.get("/inbox/admin/users").text
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert m, "🔴 後台頁沒有 csrf_token 的 hidden input"
    return m.group(1)


# ═══════════════════════════════════════════════════════════════════
# 1. 🔴 列舉式守門:每一個收表單的 POST 路由都要驗 CSRF
# ═══════════════════════════════════════════════════════════════════
def test_every_form_post_route_requires_csrf(app_client, db_session):
    """**列舉**所有收表單的 POST 路由:無 token → 403、有 token → 不是 403。

    🔴 兩個方向都要驗:
      - 只驗「無 token → 403」的話,一個**所有表單 POST 一律 403** 的實作
        會讓守門全綠,而後台**完全不能用**;
      - 只驗「有 token → 通」的話,根本沒在驗 CSRF。

    🔴 並斷言找到的路由數 ≥3 —— 列舉不到任何路由時,這支測試會全綠
       而**什麼都沒驗**(與 D01 的「新文件沒登記進 TARGETS」同一種空過)。
    """
    http, transport = app_client
    _login(http, transport)
    _grant_all(db_session)
    token = _csrf_of(http)

    routes = _form_post_routes(http.app)
    assert len(routes) >= 3, f"只列舉到 {len(routes)} 支收表單的 POST 路由:{routes}"

    for path, fields in routes:
        payload = _dummy_payload(fields)

        without = http.post(path, data=payload, follow_redirects=False)
        # 🔴 先排除 404:路徑算錯的話「沒帶 token 也不是 403」會被誤讀成
        #    「這個路由不需要 CSRF」。`iter_routes` 依賴 FastAPI 內部結構,
        #    這一行就是它算錯前綴時的警報。
        assert without.status_code != 404, (
            f"🔴 {path} 打不到(404)—— iter_routes 算出的路徑不對"
        )
        assert without.status_code == 403, (
            f"🔴 {path} 沒帶 CSRF token 卻得到 {without.status_code}"
        )

        with_token = http.post(
            path, data={**payload, "csrf_token": token}, follow_redirects=False
        )
        assert with_token.status_code != 403, (
            f"🔴 {path} 帶了正確的 CSRF token 仍然 403 —— 這一頁沒人用得了"
            f"({with_token.text[:150]})"
        )


def test_csrf_failure_is_a_distinct_error_code(app_client, db_session):
    """CSRF 失敗的錯誤碼要與「沒權限」**分得開**。

    🔴 兩者都是 403,而下一步完全不同:一個是「重新載入頁面再送」,
       另一個是「找管理員開通」。回同一個代碼會讓查問題的人分不出來
       ——第九條9 的「錯誤要寫位置、原因、修法」在這裡的落點。
    """
    http, transport = app_client
    _login(http, transport)
    _grant_all(db_session)

    r = http.post(ADMIN_FORMS[1], data={"sub": ""}, follow_redirects=False)
    assert r.status_code == 403
    assert r.json().get("error") == "csrf_failed", f"錯誤碼不對:{r.json()}"


def test_csrf_failure_is_logged(app_client, db_session, capsys):
    """CSRF 失敗要留一行 log,而且**不記表單內容**。

    ⚠ 不留 log 的話,「使用者說他按了沒反應」查不出是 CSRF 還是別的。
    🔴 但也不能把表單內容寫進去(本專案紅線:log 只記 id、sub、事件類型)。
    """
    import json

    http, transport = app_client
    _login(http, transport)
    _grant_all(db_session)
    capsys.readouterr()

    http.post(ADMIN_FORMS[1], data={"sub": "機密內容ZZ"}, follow_redirects=False)
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]

    events = [json.loads(ln) for ln in lines]
    assert any(e.get("event") == "csrf_rejected" for e in events), f"沒有 csrf_rejected:{events}"
    assert "機密內容ZZ" not in "".join(lines), "🔴 表單內容進了 log"


# ═══════════════════════════════════════════════════════════════════
# 2. 🔴 兩個後台表單:403 之後不得有任何副作用
# ═══════════════════════════════════════════════════════════════════
def test_role_change_without_csrf_has_no_effect(app_client, db_session):
    """無 CSRF 的「派角色」→ 403,**且角色沒有被改**。

    🔴 只驗狀態碼的話,「先寫再回 403」照樣綠 —— 而攻擊者要的那個角色
       已經到手了。這個表單能讓人**替自己開通任何角色**。
    """
    from app.models import AppUser

    http, transport = app_client
    _login(http, transport)
    _grant_all(db_session)
    # 先把 announcer 停掉,再試著用無 token 的請求把它打開
    from app.repo import set_role_enabled

    set_role_enabled(db_session, SELF_SUB, "announcer", enabled=False)
    db_session.commit()

    r = http.post(ADMIN_FORMS[0],
                  data={"sub": SELF_SUB, "role": "announcer", "enabled": "1"},
                  follow_redirects=False)
    assert r.status_code == 403, f"預期 403,實得 {r.status_code}"

    db_session.expire_all()
    user = db_session.get(AppUser, SELF_SUB)
    assert "announcer" not in user.active_roles(), "🔴 403 卻真的把角色打開了"


def test_purge_without_csrf_has_no_effect(app_client, db_session):
    """無 CSRF 的「清除顯示名稱」→ 403,**且快取還在**。

    🔴 這個表單留空 `sub` 就是**整批清除**。成功時只是一個 303,
       而管理員下一次看清單才會發現整欄變成「—」。
    """
    from app.models import AppUser

    http, transport = app_client
    _login(http, transport)
    _grant_all(db_session)
    db_session.expire_all()
    db_session.get(AppUser, SELF_SUB).display_name = "陳測試"
    db_session.commit()

    r = http.post(ADMIN_FORMS[1], data={"sub": ""}, follow_redirects=False)
    assert r.status_code == 403

    db_session.expire_all()
    assert db_session.get(AppUser, SELF_SUB).display_name == "陳測試", "🔴 403 卻真的清掉了"


# ═══════════════════════════════════════════════════════════════════
# 3. 正向:帶了 token 的後台操作要真的成功
# ═══════════════════════════════════════════════════════════════════
def test_role_change_with_csrf_works(app_client, db_session):
    """帶了 token 的「派角色」要**真的生效**。

    ⚠ 沒有這一條的話,一個「一律 403」的實作會讓上面每一支測試都綠,
      而後台從此不能用 —— 而那不會有錯誤訊息,只會有一個抱怨的管理員。
    """
    from app.models import AppUser
    from app.repo import set_role_enabled

    http, transport = app_client
    _login(http, transport)
    _grant_all(db_session)
    set_role_enabled(db_session, SELF_SUB, "announcer", enabled=False)
    db_session.commit()

    r = http.post(ADMIN_FORMS[0],
                  data={"sub": SELF_SUB, "role": "announcer", "enabled": "1",
                        "csrf_token": _csrf_of(http)},
                  follow_redirects=False)
    assert r.status_code == 303, f"預期 303,實得 {r.status_code} {r.text[:200]}"

    db_session.expire_all()
    assert "announcer" in db_session.get(AppUser, SELF_SUB).active_roles()


def test_admin_page_has_csrf_in_every_form(app_client, db_session):
    """後台頁上**每一個** `<form>` 都要有 csrf 的 hidden input。

    🔴 後端驗了而模板忘了放,症狀是**那個按鈕從此無效** —— 而它回 403,
       看起來像權限問題。這一條把兩邊綁在一起。
    """
    http, transport = app_client
    _login(http, transport)
    _grant_all(db_session)
    html = http.get("/inbox/admin/users").text

    forms = re.findall(r"<form\b.*?</form>", html, re.S)
    assert forms, "後台頁沒有任何 <form> —— 這支測試會變成空檢查"
    for form in forms:
        m = re.search(r'name="csrf_token"\s+value="([^"]*)"', form)
        assert m, f"🔴 有一個表單沒帶 csrf_token:{form[:160]}"
        # 🔴 **值也要驗,不能只驗欄位名在不在。** 突變檢查抓到的:
        #    把 `"csrf_token": csrf_token_for(request)` 改成 `""` 之後,
        #    `name="csrf_token"` 仍然在頁面上,而每一次送出都會 403
        #    —— **後台完全不能用,而這支測試是綠的**。
        assert re.fullmatch(r"[0-9a-f]{64}", m.group(1)), (
            f"🔴 csrf_token 的值不是 HMAC 輸出:{m.group(1)!r}"
        )
