# -*- coding: utf-8 -*-
"""CSRF:一個產 token 的函式 + 一個驗 token 的相依(T10b)。

用途: 讓**每一個接受表單的 POST 路由**用一行 `Depends(require_csrf)` 就受保護。
副作用: 驗證失敗時寫一行 log(只記 `sub` 與路徑,**不記表單內容**)。

🔴 **為什麼抽成一處(T10b),而不是留在各個 handler 裡(T09b 原本的做法):**
   T09b 把比對寫在發布頁的 handler 裡,而**角色後台那兩個表單當時沒人想到**
   —— 它們從 T05 就存在,而那時本專案還沒有任何 CSRF 機制。
   洞不是新破的,是**一直開著而沒有人注意到**。
   抽成相依之後,守門(`tests/test_csrf.py`)可以**列舉**所有收表單的路由
   逐一驗證,新表單因此自動被涵蓋。

🔴 **為什麼 JSON 端點刻意不要求 CSRF:**
   `application/x-www-form-urlencoded` 是 CORS 的「簡單內容型別」,
   跨站 `<form>` **送得出去**;而 `application/json` 會觸發預檢請求並被擋掉。
   所以風險面是「這個路由收不收表單」,不是「它會不會寫資料」。
   ⚠ 若日後有端點改成接受 `text/plain` 或 `multipart`,它也進入風險面
   —— 守門的判準要跟著改(判準寫在 `tests/test_csrf.py::_form_post_routes`)。

⚠ token 本身怎麼算:見 `app/session.py::csrf_token`
   (HMAC、單向、綁 session)。本檔只負責「從哪裡拿」與「什麼時候拒」。
"""

from __future__ import annotations

from fastapi import Form, Request

from app.oidc import OidcError, log_event

# 表單欄位名。模板與相依共用這個常數,避免兩邊寫不一樣的字串
# —— 不一樣的症狀是「那個按鈕從此無效」,而它回 403,看起來像權限問題。
CSRF_FIELD = "csrf_token"


class CsrfFailed(OidcError):
    """CSRF 驗證失敗 → **403**,而錯誤碼與「沒權限」**分開**。

    🔴 兩者都是 403,而下一步完全不同:
      - `csrf_failed` → 重新載入頁面再送(通常是頁面開太久、session 換了);
      - `forbidden`   → 找管理員開通。
    回同一個代碼會讓查問題的人分不出來,而他會往錯的方向找一整天。
    """

    def __init__(self) -> None:
        super().__init__(403, "csrf_failed", "CSRF token 不符;請重新載入頁面後再送出")


def session_key_of(request: Request) -> str | None:
    """取當前 session 的索引鍵(CSRF token 綁在它上面)。

    回傳: 索引鍵;取不到回 None
    副作用: 無

    🔴 直接 `unseal` cookie,**不呼叫 `resolve_session`** —— 能力相依已經驗過
       session 了,再跑一次會讓契約 §3.3 的主動續期在同一個請求裡發生兩次。
    """
    from app.session import SESSION_COOKIE

    store = getattr(request.app.state, "session_store", None)
    if store is None:
        return None
    return store.unseal(request.cookies.get(SESSION_COOKIE))


def csrf_token_for(request: Request) -> str:
    """產這個 session 的 CSRF token(給模板算繪成 hidden input)。

    回傳: 十六進位字串;無 session 時回空字串
    副作用: 無

    ⚠ 無 session 時回空字串而不是拋錯:算繪這個值的頁面本來就要求登入,
      所以走到這裡沒有 session 是不該發生的;而讓它變成一個**驗不過的空值**
      比讓頁面 500 更好查 —— 使用者會拿到 403 而不是白畫面。
    """
    key = session_key_of(request)
    if key is None:
        return ""
    return request.app.state.session_store.csrf_token(key)


def require_csrf(request: Request, csrf_token: str = Form("")) -> None:
    """FastAPI 相依:驗表單裡的 CSRF token,不符即 403。

    用法:
        @router.post("/x")
        def handler(_=Depends(require_csrf), ...): ...

    副作用: 失敗時寫一行 log(`csrf_rejected`,只含 `sub` 與路徑)
    錯誤: token 缺、錯、或屬於別的 session → `CsrfFailed`(403)

    🔴 **相依在 handler 之前執行**,所以拒絕發生在任何寫入之前 ——
       這正是「403 之後不得有副作用」的實作基礎,而不是靠每個 handler 自律。

    🔴 **沒有 session 時回 401,不是 403。**(平台紅線:401=憑證無效、
       403=已認證但無此權限,**不得混用**。)
       ⚠ 這是實作 T10b 時真的踩到的:第一版把「沒有 session」也當成
       CSRF 失敗而回 403,於是**未登入的 POST 從 401 變成 403** ——
       呼叫方會以為自己是「登入了但沒權限」,往完全錯的方向查。
       抓到它的是 T09b 留下的 `test_unauthenticated_is_401`,
       也就是**既有測試當安全網**(第三條4)真的發揮作用的一次。
    """
    key = session_key_of(request)
    store = getattr(request.app.state, "session_store", None)
    if key is None or store is None or store.get(key) is None:
        # 未登入 / session 已失效 → 401,交給後面的相依給出一致的答案
        raise OidcError(401, "not_authenticated", "無有效 session")
    if not store.verify_csrf(key, csrf_token):
        # 🔴 只記路徑與身分,**不記表單內容**(本專案紅線)
        log_event("csrf_rejected", sub=store.get(key).sub, path=request.url.path)
        raise CsrfFailed()
