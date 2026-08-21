# -*- coding: utf-8 -*-
"""T04 OIDC 登入紅測試。

對應驗收:`docs/任務表.md` T04、`docs/TDD測試計畫表.md` §3、
《帳號系統接入契約》§3.1/§3.2/§3.3/§4.1/§4.10。

🔴 這一組測試不是照「函式有沒有回傳正確值」寫的,是照契約列出的
**三個「不會有錯誤訊息」的坑**反推的:

| 坑 | 症狀 | 對應測試 |
|---|---|---|
| `redirect_uri` 與 client 登記值不逐字相同 | 錯誤停在 Keycloak 頁面,**自家 log 是空的** | `test_redirect_uri_matches_registered_value` |
| 不做 refresh | 登入 5 分鐘後**靜默**退回未登入,**伺服器零錯誤** | `test_server_side_refresh_before_access_token_expiry` |
| 測試自造的 token 少一個真實 IdP 會給的 claim | 400 多支離線測試全過,**第一個真人登入 100% 失敗** | `test_fake_idp_token_carries_real_claims` |
"""

from __future__ import annotations

import importlib
import urllib.parse

import pytest

from tests.fake_idp import CLIENT_ID, ISSUER, REGISTERED_REDIRECT_URI, FakeIdP, at_hash_for

# ── 測試時鐘 ──────────────────────────────────────────────────────
# 固定起點 + 可推進。用真實 time.time() 的測試會讓「過期 29 秒」這種
# 邊界斷言隨機失敗,而失敗看起來像程式錯。
T0 = 1_780_000_000.0


class Clock:
    """可推進的測試時鐘。"""

    def __init__(self, start: float = T0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture(scope="module")
def idp() -> FakeIdP:
    """Keycloak 替身(兩把鑰,模擬輪替期新舊並存)。

    module 級:RSA 2048 產生一次約 0.1–0.5 秒,每個 test 都產會讓這支
    測試檔慢到沒人願意跑——沒人跑的測試等於沒有測試。
    """
    return FakeIdP.build()


@pytest.fixture()
def clock() -> Clock:
    return Clock()


class FakeTransport:
    """OIDC 端點替身(discovery / JWKS / token)。

    用途: 讓驗證與續期邏輯在無網路、無 secret 的情況下可測。
    副作用: 無。所有呼叫次數記在自己身上供斷言。
    """

    def __init__(self, idp: FakeIdP, clock: Clock, *, jwks_only=("kid-old", "kid-new")) -> None:
        self.idp = idp
        self.clock = clock
        self.jwks_only = jwks_only
        self.token_calls: list[dict] = []
        self.discovery_calls = 0
        # 由測試改寫:模擬 refresh 失敗(帳號被停用 → refresh 立刻失效)
        self.refresh_fails = False
        # 登入流程產生的是**隨機** nonce,替身簽 token 時必須用同一個,
        # 否則 callback 會因 nonce 不符而拒絕——那是對的行為,不是測試該繞過的。
        self.next_nonce = "test-nonce"

    # --- app.oidc.Transport 介面 ---
    def get_json(self, url: str) -> dict:
        if url.endswith("/.well-known/openid-configuration"):
            self.discovery_calls += 1
            return self.idp.discovery()
        if url.endswith("/certs"):
            return self.idp.jwks(only=self.jwks_only)
        raise AssertionError(f"測試替身未預期的 GET:{url}")

    def post_form(self, url: str, data: dict) -> tuple[int, dict]:
        self.token_calls.append(dict(data))
        grant = data.get("grant_type")
        if grant == "refresh_token" and self.refresh_fails:
            # Keycloak 停用帳號後 refresh 的實際回應形狀
            return 400, {"error": "invalid_grant", "error_description": "Session not active"}
        if grant not in ("authorization_code", "refresh_token"):
            return 400, {"error": "unsupported_grant_type"}
        # 續期換來的 id_token 依規範不帶 nonce(我方也刻意不比對)
        nonce = self.next_nonce if grant == "authorization_code" else None
        return 200, self.token_response(nonce=nonce)

    # --- 輔助 ---
    def token_response(self, *, expires_in: int = 300, nonce: str | None = "test-nonce") -> dict:
        access = f"access-{int(self.clock())}"
        return {
            "token_type": "Bearer",
            "access_token": access,
            "refresh_token": f"refresh-{int(self.clock())}",
            "expires_in": expires_in,
            "id_token": self.idp.id_token(
                now=self.clock(), kid="kid-new", nonce=nonce, access_token=access,
                expires_in=expires_in,
            ),
        }


@pytest.fixture()
def oidc(idp, clock, monkeypatch):
    """建立指向替身的 OidcClient。

    回傳: (client, transport)
    副作用: 以 monkeypatch 設定 OIDC 環境變數,測試結束自動還原。
    """
    _set_oidc_env(monkeypatch)
    import app.config as config

    config.get_settings.cache_clear()
    importlib.reload(config)

    from app.oidc import OidcClient

    transport = FakeTransport(idp, clock)
    return OidcClient(config.get_settings(), transport=transport, clock=clock), transport


def _set_oidc_env(monkeypatch) -> None:
    """把 OIDC 相關 env 設成與 portal 已建立的 client 一致的值。"""
    monkeypatch.setenv("INBOX_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("INBOX_OIDC_INTERNAL_BASE", "http://keycloak:8080/auth")
    monkeypatch.setenv("INBOX_OIDC_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("INBOX_OIDC_CLIENT_SECRET", "test-only-not-a-real-secret")
    monkeypatch.setenv("INBOX_SESSION_SECRET", "test-only-session-secret-0123456789")
    monkeypatch.setenv("INBOX_PUBLIC_BASE_URL", "https://catsapp.sporton.com.tw")


@pytest.fixture()
def app_client(idp, clock, monkeypatch):
    """建立掛好 auth 路由的 TestClient。

    回傳: (TestClient, transport)
    副作用: 設定 env、清 settings 快取;測試結束還原。

    為什麼要 `create_app()` 而不是直接 import `app.main.app`:
    OIDC 路由是否註冊取決於 env(未設 issuer 時不註冊,見 T04 回滾方式),
    而 `app.main` 在別的測試檔裡可能已經被 import 過、設定已被快取——
    那會讓本檔的測試依賴「pytest 先跑哪一支」,而那種依賴的失敗是隨機的。
    """
    from fastapi.testclient import TestClient

    _set_oidc_env(monkeypatch)
    import app.config as config

    config.get_settings.cache_clear()

    import app.main as main

    importlib.reload(main)
    transport = FakeTransport(idp, clock)
    application = main.create_app(transport=transport, clock=clock)
    yield TestClient(application, base_url="https://testserver"), transport
    config.get_settings.cache_clear()


# ═══════════════════════════════════════════════════════════════════
# 1. 演算法
# ═══════════════════════════════════════════════════════════════════
def test_rejects_hs256_and_none_alg(oidc, idp, clock):
    """`alg=HS256` 與 `alg=none` 的 token 一律 401(契約 §3.2 只接受 RS256)。

    為什麼這條是紅線:HS256 是對稱簽章,任何知道 client secret 的人
    (包含 IdP 以外的第三方 App)都能自簽出一個「合法」身分;
    `alg=none` 更是連簽章都不要。放行等於整套身分驗證不存在。
    """
    from app.oidc import OidcError

    for alg in ("HS256", "none"):
        token = idp.id_token(now=clock(), alg=alg)
        with pytest.raises(OidcError) as e:
            oidc[0].verify_id_token(token, nonce="test-nonce")
        assert e.value.status_code == 401, f"alg={alg} 應為 401,實得 {e.value.status_code}"


# ═══════════════════════════════════════════════════════════════════
# 2. aud
# ═══════════════════════════════════════════════════════════════════
def test_rejects_wrong_audience(oidc, idp, clock):
    """`aud` 不是本 client_id 一律 401——防拿 A app 的 token 打 B app。"""
    from app.oidc import OidcError

    token = idp.id_token(now=clock(), aud="some-other-app")
    with pytest.raises(OidcError) as e:
        oidc[0].verify_id_token(token, nonce="test-nonce")
    assert e.value.status_code == 401


# ═══════════════════════════════════════════════════════════════════
# 3. exp ±30s(雙向)
# ═══════════════════════════════════════════════════════════════════
def test_rejects_expired_token_with_30s_leeway(oidc, idp, clock):
    """過期 29 秒要**通過**、過期 31 秒要 **401**。

    🔴 只測一邊等於沒測 leeway:只測「過期就拒絕」會讓 leeway=0 也全綠,
    而 leeway=0 的症狀是**時鐘差幾秒就隨機登不進來**(契約 §3.3 的 ±30s
    是**驗證方**的義務,Keycloak realm 沒有這個開關)。
    """
    from app.oidc import OidcError

    client, _ = oidc

    ok = idp.id_token(now=clock(), expires_in=-29)
    claims = client.verify_id_token(ok, nonce="test-nonce")
    assert claims["sub"], "過期 29 秒仍在 ±30s 容忍內,必須通過"

    bad = idp.id_token(now=clock(), expires_in=-31)
    with pytest.raises(OidcError) as e:
        client.verify_id_token(bad, nonce="test-nonce")
    assert e.value.status_code == 401


# ═══════════════════════════════════════════════════════════════════
# 4. JWKS kid 輪替 + 1h 快取
# ═══════════════════════════════════════════════════════════════════
def test_jwks_supports_kid_rotation(idp, clock, monkeypatch):
    """JWKS 有兩把鑰時依 `kid` 選對;遇未知 kid 必須重抓一次。

    🔴 假設只有一把鑰,會在**金鑰輪替當天全掛**,而那天沒有人在改程式
    ——查的人會先懷疑自己的程式,不會先懷疑 IdP 換了鑰。
    """
    from app.oidc import OidcClient, OidcError

    _set_oidc_env(monkeypatch)
    import app.config as config

    config.get_settings.cache_clear()

    # `idp` 是 module 級 fixture(RSA 產一次就好),計數器因此跨 test 累加。
    # 本測試斷言的是「抓了幾次」,故先歸零——不歸零的話這支測試會隨
    # **pytest 先跑哪幾支**而失敗,而那種失敗看起來像程式有 bug。
    idp.jwks_calls = 0

    # 起始:IdP 只公開舊鑰
    transport = FakeTransport(idp, clock, jwks_only=("kid-old",))
    client = OidcClient(config.get_settings(), transport=transport, clock=clock)

    old = idp.id_token(now=clock(), kid="kid-old")
    client.verify_id_token(old, nonce="test-nonce")
    assert idp.jwks_calls == 1, "第一次驗證必須抓一次 JWKS"

    # 同一把鑰再驗:1h 內不得重抓(契約要求快取 1h)
    client.verify_id_token(idp.id_token(now=clock(), kid="kid-old"), nonce="test-nonce")
    assert idp.jwks_calls == 1, "快取失效:同一個 kid 在 1h 內被重抓了"

    # IdP 輪替:新鑰上線(輪替不會發生在同一微秒,故推進時鐘 2 分鐘)
    clock.advance(120)
    transport.jwks_only = ("kid-old", "kid-new")
    new = idp.id_token(now=clock(), kid="kid-new")
    client.verify_id_token(new, nonce="test-nonce")
    assert idp.jwks_calls == 2, "遇未知 kid 必須重抓一次 JWKS"

    # 未知 kid 反覆送:401,且**不得每次都去打 IdP**
    # 🔴 少了這條,任何人送一串亂編的 kid 就能讓本服務對 IdP 發起放大攻擊,
    #    而本服務自己的觀測是「有人登入失敗」,看不出是自己在打 IdP。
    before = idp.jwks_calls
    forged = idp.id_token(now=clock(), kid="kid-old")
    forged = forged.replace(forged.split(".")[0], _header_with_kid("kid-does-not-exist"), 1)
    for _ in range(5):
        with pytest.raises(OidcError) as e:
            client.verify_id_token(forged, nonce="test-nonce")
        assert e.value.status_code == 401
    assert idp.jwks_calls - before <= 1, "未知 kid 反覆請求不得每次都打 IdP"


def _header_with_kid(kid: str) -> str:
    """組一個帶指定 kid 的 JWT header(base64url,無 padding)。"""
    import base64
    import json

    raw = json.dumps({"alg": "RS256", "typ": "JWT", "kid": kid}).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


# ═══════════════════════════════════════════════════════════════════
# 5. 🔴 伺服器端主動 refresh(契約 §3.3 陷阱)
# ═══════════════════════════════════════════════════════════════════
def test_server_side_refresh_before_access_token_expiry(app_client, clock):
    """登入後把時鐘推過 access token 壽命(300s),頁面必須仍是登入狀態。

    🔴 這是本任務唯一「壞掉時完全沒有錯誤訊息」的缺陷:
    伺服器端算繪的 App 沒有前端 JS 會替它 refresh,而 SSO session 有 10 小時
    ——使用者的體感是「登入 5 分鐘後莫名變成未登入」,而伺服器 log 乾乾淨淨。
    upload-program 踩過(契約 §3.3),它被點名可能同病的還有 compliance 與 sporton_core。
    """
    client, transport = app_client
    _login(client, transport)

    r = client.get("/inbox/me")
    assert r.status_code == 200, f"剛登入應為 200,實得 {r.status_code}"
    sub_before = r.json()["sub"]

    clock.advance(301)  # access token 已過期,但 SSO session 還在
    r = client.get("/inbox/me")
    assert r.status_code == 200, "過 300 秒後仍須為登入狀態(伺服器端主動 refresh)"
    assert r.json()["sub"] == sub_before, "refresh 後身分不得改變"

    refreshes = [c for c in transport.token_calls if c.get("grant_type") == "refresh_token"]
    assert len(refreshes) >= 1, "沒有任何 refresh_token 呼叫 = 沒做主動續期"


def test_refresh_failure_logs_user_out(app_client, clock):
    """refresh 失敗(帳號被停用)必須立刻變未登入 → 401,而不是繼續放行。

    契約 §3.3:用 refresh 換取「收權即時性」——IdP 停用帳號時 refresh 會失敗,
    那一刻就該登出。若失敗時沿用舊 session,收權就變成假的。
    """
    client, transport = app_client
    _login(client, transport)

    transport.refresh_fails = True
    clock.advance(301)
    r = client.get("/inbox/me")
    assert r.status_code == 401, f"refresh 失敗須登出(401),實得 {r.status_code}"


# ═══════════════════════════════════════════════════════════════════
# 6. 測試替身本身的體檢
# ═══════════════════════════════════════════════════════════════════
def test_fake_idp_token_actually_expires(oidc, idp, clock):
    """替身簽出的 token 必須**真的會過期**。

    契約明文提醒:測試常用的假 token 永不過期,會把「不做 refresh」
    那個缺陷一起抹平——測試全綠而使用者 5 分鐘後掉出去。
    """
    from app.oidc import OidcError

    client, _ = oidc
    token = idp.id_token(now=clock(), expires_in=300)
    client.verify_id_token(token, nonce="test-nonce")  # 現在有效

    clock.advance(331)  # 300s 壽命 + 30s 容忍 + 1
    with pytest.raises(OidcError) as e:
        client.verify_id_token(token, nonce="test-nonce")
    assert e.value.status_code == 401, "同一個 token 在時鐘推進後必須失效"


def test_fake_idp_token_carries_real_claims(oidc, idp, clock):
    """替身必須給滿真實 Keycloak 會給的 claims,且我方對 `at_hash` 的行為必須明確。

    🔴 契約 v3.2 的 PLM 事故:開旗標當天**第一個真人登入 100% 失敗**於
    `No access_token provided to compare against at_hash claim.`
    ——Keycloak 的 id_token 帶 `at_hash`,而 PLM 的驗證呼叫沒有地方傳 access_token。
    **PLM 那四百多支離線測試一支都沒抓到,因為測試自己造的 token 沒有那個 claim。**

    本測試釘三件事:
      ① 替身確實帶 `at_hash`/`sid`/`azp`/`nonce`(否則以下兩條都是裝飾);
      ② `at_hash` 存在但我方**沒有** access_token 時 → **不得拋錯**(明確選擇:跳過);
      ③ `at_hash` 與 access_token **不符**時 → 401(這才是它存在的意義)。
    """
    from app.oidc import OidcError

    client, _ = oidc
    access = "the-real-access-token"
    token = idp.id_token(now=clock(), access_token=access)

    import jwt as pyjwt

    raw = pyjwt.decode(token, options={"verify_signature": False})
    for claim in ("at_hash", "sid", "azp", "nonce", "iat", "auth_time", "typ"):
        assert claim in raw, f"替身少了真實 IdP 會給的 claim:{claim}"
    assert raw["at_hash"] == at_hash_for(access)

    # ② 沒有 access_token 可比 → 明確跳過,不拋錯(PLM 當天炸的就是這條)
    claims = client.verify_id_token(token, nonce="test-nonce")
    assert claims["sub"], "at_hash 存在但無 access_token 時不得拋錯"

    # ③ 有 access_token 但不符 → 401
    with pytest.raises(OidcError) as e:
        client.verify_id_token(token, nonce="test-nonce", access_token="a-different-token")
    assert e.value.status_code == 401, "at_hash 不符必須拒絕,否則帶它等於沒帶"


# ═══════════════════════════════════════════════════════════════════
# 7. 🔴 redirect_uri 逐字比對
# ═══════════════════════════════════════════════════════════════════
def test_redirect_uri_matches_registered_value(oidc, app_client):
    """程式實際組出的 `redirect_uri` 必須**逐字等於** client 的登記值。

    🔴 契約 v2.14 的 PLM 事故:Django `reverse()` 回**後註冊者**,
    實際送出的 `redirect_uri` 與登記值不同 → **首次登入就 mismatch**,
    而錯誤停在 Keycloak 的頁面、**PLM 自己的 log 是空的**——查的人會先懷疑錯地方。

    OAuth 對 `redirect_uri` 是逐字比對:多一個斜線就是不同的值。
    登記值帶結尾斜線(`.../inbox/oidc/callback/`),所以這裡也必須帶。
    """
    client, _ = oidc
    assert client.redirect_uri == REGISTERED_REDIRECT_URI, (
        f"逐字不符\n  程式組出:{client.redirect_uri}\n  client 登記:{REGISTERED_REDIRECT_URI}"
    )

    # 不只驗屬性,連實際送去 IdP 的查詢字串也要驗——屬性對而組 URL 時漏掉才是真實故障
    http, _t = app_client
    r = http.get("/inbox/oidc/login", follow_redirects=False)
    assert r.status_code == 302, f"登入端點應 302 到 IdP,實得 {r.status_code}"
    q = urllib.parse.parse_qs(urllib.parse.urlparse(r.headers["location"]).query)
    assert q["redirect_uri"] == [REGISTERED_REDIRECT_URI]


# ═══════════════════════════════════════════════════════════════════
# 8. PKCE S256
# ═══════════════════════════════════════════════════════════════════
def test_login_route_issues_pkce_s256(app_client):
    """登入導向必須帶 `code_challenge_method=S256`、一次性 `state` 與 `nonce`。

    紅線:平台一律 Authorization Code + **PKCE**。
    `plain` 的 challenge 等於沒有 PKCE,而它會**照樣登入成功**——
    也就是說少了這條斷言,退化不會有任何症狀。
    並斷言 `code_verifier` **不得**出現在導向 URL 裡(它必須只留在伺服器端)。
    """
    http, _ = app_client
    r = http.get("/inbox/oidc/login", follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    q = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)

    assert q["response_type"] == ["code"]
    assert q["client_id"] == [CLIENT_ID]
    assert q["code_challenge_method"] == ["S256"], "PKCE 必須是 S256,plain 等於沒有 PKCE"
    assert q["code_challenge"][0], "缺 code_challenge"
    assert q["state"][0] and q["nonce"][0], "缺 state/nonce(CSRF 與重放防護)"
    assert "code_verifier" not in loc, "🔴 code_verifier 洩漏到瀏覽器,PKCE 失效"
    # 刻意不申請 email:portal 已從 client 的預設 scope 移除(申請書〈貳〉)
    assert "email" not in q.get("scope", [""])[0].split()

    # 導向的是**對外** issuer(§2.4:內部位址只用於伺服器自己連,不給瀏覽器)
    assert loc.startswith(ISSUER), f"登入導向必須是對外 issuer,實得 {loc[:80]}"


def test_state_mismatch_is_rejected(app_client, clock):
    """callback 的 `state` 與伺服器端存的不符 → 400,且不得建立 session。"""
    http, transport = app_client
    http.get("/inbox/oidc/login", follow_redirects=False)
    r = http.get(
        "/inbox/oidc/callback/",
        params={"code": "whatever", "state": "not-the-state-we-issued"},
        follow_redirects=False,
    )
    assert r.status_code == 400, f"state 不符應 400,實得 {r.status_code}"
    assert http.get("/inbox/me").status_code == 401, "state 不符卻建立了 session"


# ═══════════════════════════════════════════════════════════════════
# 9. 回滾閥門:設定不齊備時不註冊 auth 路由
# ═══════════════════════════════════════════════════════════════════
def test_auth_routes_absent_without_issuer(monkeypatch):
    """未設 `INBOX_OIDC_ISSUER` 時,登入路由**不得存在**(404),健康檢查照常。

    這是 T04 的回滾閥門(比照 portal 對 PLM 要求的「旗標預設 off、
    off 時行為逐字不變」)。它讓「部署了但 secret 還沒到」是一個
    **明確狀態**(404),而不是使用者點下登入才炸的 500。
    """
    from fastapi.testclient import TestClient

    for var in ("INBOX_OIDC_ISSUER", "INBOX_SESSION_SECRET"):
        monkeypatch.delenv(var, raising=False)
    import app.config as config

    config.get_settings.cache_clear()
    import app.main as main

    importlib.reload(main)
    http = TestClient(main.create_app())
    assert http.get("/inbox/health").status_code == 200, "OIDC 未設定不得影響健康檢查"
    assert http.get("/inbox/oidc/login").status_code == 404, "設定不齊備時不得註冊登入路由"
    assert http.get("/inbox/me").status_code == 404
    config.get_settings.cache_clear()


# ═══════════════════════════════════════════════════════════════════
# 輔助:走完一次登入
# ═══════════════════════════════════════════════════════════════════
def _login(http, transport) -> None:
    """走完 login → callback,讓 TestClient 拿到 session cookie。

    副作用: 在 http 的 cookie jar 留下 session cookie。
    """
    r = http.get("/inbox/oidc/login", follow_redirects=False)
    assert r.status_code == 302, f"login 應 302,實得 {r.status_code}"
    q = urllib.parse.parse_qs(urllib.parse.urlparse(r.headers["location"]).query)
    # 替身要用**這一次**的 nonce 簽 token;用固定值會讓 nonce 比對永遠不過
    transport.next_nonce = q["nonce"][0]
    r = http.get(
        "/inbox/oidc/callback/",
        params={"code": "a-valid-code", "state": q["state"][0]},
        follow_redirects=False,
    )
    assert r.status_code == 302, f"callback 應 302 回 /inbox/,實得 {r.status_code} {r.text[:200]}"
