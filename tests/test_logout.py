# -*- coding: utf-8 -*-
"""T06 登出雙向紅測試(自發登出 + SLO front-channel)。

對應驗收:`docs/任務表.md` T06、`docs/TDD測試計畫表.md` §3、
《帳號系統接入契約》§10.1–§10.7。

🔴 本檔刻意**不測「端點有沒有被 IdP 呼叫」**——那件事測不到,而契約記了
   三種原因都會讓它「零呼叫」,且觀測結果完全相同:

| 原因 | 觀測 | 出處 |
|---|---|---|
| 登記值與 route 差一個字元 | 端點零呼叫 | §10.3a |
| portal 沒把設定套用到 live realm | 端點零呼叫 | §10.7 |
| 測試時沒走 OIDC 交換(舊 cookie 放行) | 端點零呼叫 | §10.7 |

   三者都只是「什麼都沒發生」。所以這裡測的是**我方那一端**能被確定的東西:
   route 字串逐字、行為冪等、副作用只有刪、以及拿 `sid` 殺得掉 session。
"""

from __future__ import annotations

import urllib.parse

from tests.conftest import _login
from tests.fake_idp import ISSUER

# client 已登記的兩個 post-logout 值(`idp/bootstrap/client-cats-inbox.sh`)。
# 我方選用第二個——見 dev-log 2026-08-24 的「② 盤點文件 §4 Q1 更正」。
REGISTERED_POST_LOGOUT = "https://catsapp.sporton.com.tw/inbox/logged-out/"
# 🔴 front-channel 登記值:**無結尾斜線**(§10.3a——真正的規格是「與 route 逐字相同」)
FRONTCHANNEL_PATH = "/inbox/oidc/frontchannel-logout"


# ═══════════════════════════════════════════════════════════════════
# 1. 🔴 route 字串逐字(不依賴 redirect 行為)
# ═══════════════════════════════════════════════════════════════════
def test_frontchannel_route_is_registered_verbatim(app_client):
    """route 必須**逐字**是 `/inbox/oidc/frontchannel-logout`(無結尾斜線)。

    斷言方式是**行為**,而且刻意不跟隨 redirect:

    | 打無斜線的登記值 | 意義 |
    |---|---|
    | **204** | route 逐字相符 ✅ |
    | 307 | 我方 route 帶了斜線 → 與登記值不符,🔴 就是 §10.3a 那個坑 |
    | 404 | route 不存在或路徑寫錯 |

    再打帶斜線的變體,預期 **307**——若它也直接回 204,表示**兩個變體都註冊了**,
    那「登記值指到哪一個」就變成看運氣。

    🔴 兩件事說明白:

    ① **原規格(TDD 計畫表 T06)寫「帶斜線變體必須 404」,本次判定該規格是錯的。**
       實測 FastAPI 對帶斜線變體回 307 並**保留 `sid`/`iss` 查詢字串**,而 iframe
       會跟隨 307。那個 redirect 在正式環境是**安全網**:portal 萬一登記成有斜線的
       變體,307 讓它照樣生效,而不是變成「端點零呼叫」。改成 404 在正式環境
       買不到任何東西,只讓一個登記錯字變成致命。

    ② **不用「列舉 `app.routes`」來斷言路徑字串。** 本版 FastAPI(0.141)把
       `include_router` 的結果包成一個 `_IncludedRouter` 物件,列舉頂層只拿到
       自動產生的 `/docs`、`/openapi.json`,**拿不到任何實際端點**。靠內部結構
       寫的斷言會在升版時悄悄改變它在檢查什麼——行為斷言不會。
    """
    http, _ = app_client
    r = http.get(FRONTCHANNEL_PATH, follow_redirects=False)
    assert r.status_code == 204, (
        f"打 client 登記值得到 {r.status_code}(預期 204)。"
        f"307=我方 route 多了結尾斜線、404=路徑不存在\n  登記值:{FRONTCHANNEL_PATH}"
    )
    r = http.get(FRONTCHANNEL_PATH + "/", follow_redirects=False)
    assert r.status_code == 307, (
        f"帶斜線的變體預期 307(導到無斜線的正式 route),實得 {r.status_code}。"
        "若為 204 表示兩個變體都註冊了,登記值指到哪一個變成看運氣"
    )
    # 307 必須保留 Keycloak 帶的參數,否則「跟隨後才生效」這條安全網是假的
    r = http.get(FRONTCHANNEL_PATH + "/", params={"sid": "s1", "iss": ISSUER},
                 follow_redirects=False)
    assert "sid=s1" in r.headers["location"], f"307 掉了 sid:{r.headers['location']}"


# ═══════════════════════════════════════════════════════════════════
# 2. 免認證 + 冪等(§10.3)
# ═══════════════════════════════════════════════════════════════════
def test_frontchannel_logout_is_idempotent_and_unauthenticated(app_client):
    """免認證呼叫 → 204;重複呼叫 → 204;帶或不帶 `iss`/`sid` 皆 204。

    契約 §10.3:iframe 載入時**不會帶**你的 API token,所以端點必須免認證;
    而 Keycloak 可能重送、也可能省略參數,所以必須冪等且參數可有可無。
    """
    http, _ = app_client

    # ① 完全沒登入、沒 cookie
    r = http.get(FRONTCHANNEL_PATH)
    assert r.status_code == 204, f"免認證呼叫應 204,實得 {r.status_code}"

    # ② 重複呼叫
    assert http.get(FRONTCHANNEL_PATH).status_code == 204

    # ③ 帶 Keycloak 實際會帶的兩個參數
    r = http.get(FRONTCHANNEL_PATH, params={"iss": ISSUER, "sid": "sid-abc-123"})
    assert r.status_code == 204

    # ④ 只帶其中一個(無狀態的 App 無從比對 sid,忽略即可——但不得因此炸)
    assert http.get(FRONTCHANNEL_PATH, params={"sid": "whatever"}).status_code == 204
    assert http.get(FRONTCHANNEL_PATH, params={"iss": ISSUER}).status_code == 204


def test_frontchannel_logout_response_is_not_cacheable(app_client):
    """登出回應必須 `Cache-Control: no-store`。

    被快取的登出等於登出無效,而症狀是**「有時登得出、有時登不出」**
    ——那種間歇性症狀沒有人查得動。
    """
    http, _ = app_client
    r = http.get(FRONTCHANNEL_PATH)
    assert "no-store" in r.headers.get("cache-control", ""), (
        f"缺 no-store,實得 Cache-Control={r.headers.get('cache-control')!r}"
    )


# ═══════════════════════════════════════════════════════════════════
# 3. 🔴 副作用只有「刪」(§10.3)
# ═══════════════════════════════════════════════════════════════════
def test_frontchannel_logout_only_deletes_cookie(app_client):
    """端點不得建立 session、不得寫任何業務資料。

    契約 §10.3:「**被任意第三方呼叫的最壞後果必須是使用者被登出**」。
    這個端點是免認證的,任何人都能打——所以它能做的事必須只有「刪」。
    斷言方式:session 數只減不增,且不得產生新的登入交易。
    """
    http, _ = app_client
    store = http.app.state.session_store

    before_sessions = len(store._sessions)
    before_pending = len(store._pending)

    for params in ({}, {"sid": "sid-abc-123"}, {"sid": "not-a-real-sid"}):
        assert http.get(FRONTCHANNEL_PATH, params=params).status_code == 204

    assert len(store._sessions) <= before_sessions, "front-channel 端點建立了 session"
    assert len(store._pending) <= before_pending, "front-channel 端點建立了登入交易"

    # 回應必須帶刪除 session cookie 的 Set-Cookie(iframe 是同站,寫得進去)
    r = http.get(FRONTCHANNEL_PATH)
    set_cookie = r.headers.get("set-cookie", "")
    assert "inbox_session" in set_cookie, f"未刪除 session cookie:{set_cookie!r}"


def test_frontchannel_logout_with_sid_kills_that_session(app_client, clock):
    """拿 IdP 的 `sid`、**不帶瀏覽器 cookie** 也必須殺得掉那個 session。

    🔴 這是 §10.3/§10.7 真正要的能力,也是 T04 決定「session 放伺服器端」
    的唯一理由:front-channel 是 **IdP 主動呼叫**,而 iframe 的請求
    **可能根本沒有那個人的 cookie**(分頁凍結、cookie 政策、或 IdP 從別的
    脈絡發起)。只靠 cookie 刪除的實作,在那種情況下會**靜默不生效**
    ——使用者以為登出了,而 inbox 還是登入狀態。
    """
    from fastapi.testclient import TestClient

    http, transport = app_client
    _login(http, transport)
    assert http.get("/inbox/me").status_code == 200, "前置:應已登入"

    # 另開一個**完全沒有 cookie** 的 client,模擬 IdP 的 iframe 請求
    iframe = TestClient(http.app, base_url="https://testserver")
    r = iframe.get(FRONTCHANNEL_PATH, params={"iss": ISSUER, "sid": "sid-abc-123"})
    assert r.status_code == 204

    assert http.get("/inbox/me").status_code == 401, (
        "🔴 IdP 用 sid 通知登出後,原本那個 session 仍然有效"
    )


# ═══════════════════════════════════════════════════════════════════
# 4. 自發登出(§10 方向一)
# ═══════════════════════════════════════════════════════════════════
def test_logout_clears_local_session_before_redirecting_to_idp(app_client, clock):
    """自發登出:**先清本地 session**,再 302 到 IdP 的 `end_session`。

    🔴 順序不能反。先導 IdP 的話,使用者中途關掉分頁(或 IdP 掛了)
    本地 session 就還活著——他按了登出,而 inbox 還認得他。
    斷言方式:**不跟隨** redirect 就檢查 `/inbox/me`,若已 401 就證明
    清除發生在回應送出之前。
    """
    http, transport = app_client
    store = http.app.state.session_store
    _login(http, transport)
    assert http.get("/inbox/me").status_code == 200

    # 🔴 把 cookie 值先抄下來。只斷言「登出後 /me 是 401」是**不夠的**:
    #    刪掉瀏覽器的 cookie 也會讓 /me 變 401,而伺服器端那個 session
    #    可能還活著——抄到 cookie 的人就還能用。這一步是為了測那件事。
    captured = http.cookies.get("inbox_session")
    assert captured, "前置:應已拿到 session cookie"
    before = len(store._sessions)

    r = http.get("/inbox/logout", follow_redirects=False)
    assert r.status_code == 302, f"登出應 302 到 IdP,實得 {r.status_code}"
    assert http.get("/inbox/me").status_code == 401, "本地 session 未在導向前清除"
    assert len(store._sessions) == before - 1, (
        "🔴 伺服器端的 session 沒被刪——只刪了瀏覽器的 cookie。"
        "抄到那個 cookie 值的人仍然是登入狀態"
    )

    # 重放那個被抄走的 cookie:必須是 401
    from fastapi.testclient import TestClient

    replay = TestClient(http.app, base_url="https://testserver",
                        cookies={"inbox_session": captured})
    assert replay.get("/inbox/me").status_code == 401, (
        "🔴 登出後重放舊 cookie 仍然通行——session 只是從瀏覽器消失,沒有真的作廢"
    )

    loc = r.headers["location"]
    assert loc.startswith(ISSUER), f"必須導向**對外** issuer(§2.4),實得 {loc[:80]}"
    q = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)
    assert q.get("id_token_hint", [""])[0], (
        "缺 id_token_hint——IdP 認不出要結束哪個 session,登出會停在確認頁"
    )


def test_post_logout_redirect_uri_matches_registered_value(app_client, transport=None):
    """`post_logout_redirect_uri` 必須**逐字**等於 client 的登記值。

    🔴 與 T04 的 `redirect_uri` 同一類錯誤,而症狀更難懂:Keycloak 對
    未登記的 post-logout 值會**拒絕導回**,使用者停在 IdP 的頁面上,
    而我方的 log 只看到「他登出了」——一切看起來正常。
    """
    http, t = app_client
    _login(http, t)
    r = http.get("/inbox/logout", follow_redirects=False)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(r.headers["location"]).query)
    assert q["post_logout_redirect_uri"] == [REGISTERED_POST_LOGOUT], (
        f"逐字不符\n  程式送出:{q.get('post_logout_redirect_uri')}"
        f"\n  client 登記:{REGISTERED_POST_LOGOUT}"
    )


def test_logout_without_session_is_safe(app_client):
    """未登入按登出不得 500,也不該去打 IdP。

    沒有 session 就沒有 `id_token_hint`,拿去 `end_session` 只會得到
    一個要使用者確認的頁面。直接送他到已登出頁,語意正確也少一次往返。
    """
    http, _ = app_client
    r = http.get("/inbox/logout", follow_redirects=False)
    assert r.status_code == 302, f"應 302,實得 {r.status_code}"
    assert r.headers["location"] == "/inbox/logged-out/", (
        f"未登入時應直接到已登出頁,實得 {r.headers['location']}"
    )


# ═══════════════════════════════════════════════════════════════════
# 5. 已登出頁
# ═══════════════════════════════════════════════════════════════════
def test_logged_out_page_is_public_and_offline(app_client):
    """已登出頁:免認證 200、有重新登入連結、**零外部資源**。

    零外部資源是契約 §4.10 的同源義務(禁外部 CDN)。
    這一頁是使用者按下登出後**唯一**會看到的東西,它如果 404,
    使用者的體感就是「登出把系統弄壞了」。
    """
    http, _ = app_client
    r = http.get("/inbox/logged-out/")
    assert r.status_code == 200, f"已登出頁應 200,實得 {r.status_code}"
    body = r.text
    assert "/inbox/oidc/login" in body, "缺重新登入的連結"
    for bad in ("https://cdn", "https://fonts", "http://cdn", "unpkg", "jsdelivr"):
        assert bad not in body, f"引用了外部資源:{bad}(契約 §4.10 禁外部 CDN)"
    assert "prefers-color-scheme: dark" not in body, "第四條2:不得深色自動切換"
