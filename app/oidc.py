# -*- coding: utf-8 -*-
"""OIDC 客戶端:Authorization Code + PKCE、id_token 驗證、token 續期。

用途: 把《帳號系統接入契約》§3(token 契約)與 §4.1(PKCE)的驗證義務
      集中在一個地方,讓「有沒有照契約驗」可以被測試逐條斷言。
副作用: 對 IdP 發 HTTP 請求(discovery / JWKS / token 端點)。不寫 DB、不寫檔。

契約落點對照:
  §3.2  只接受 RS256;JWKS 快取 1h、支援 kid 輪替  → `verify_id_token` / `_key_for`
  §3.1  驗 `iss` / `aud`(= client_id)/ `exp`      → `verify_id_token`
  §3.3  時鐘容忍 ±30 秒(**驗證方**的義務)         → `LEEWAY_SECONDS`
  §3.3  伺服器端主動 refresh                        → `refresh()`(由 routes_auth 觸發)
  §2.4  伺服器走內部位址,`iss` 維持對外             → `_to_internal` / `_to_external`
  §4.1  PKCE S256、redirect_uri 逐字相符            → `authorization_url` / `redirect_uri`
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.parse
from typing import Any, Protocol

import jwt

from app import clock as clock_module

# 契約 §3.3:時鐘容忍 ±30 秒。Keycloak realm **沒有**這個開關,
# 它是驗 token 這一方該設的值——設 0 的症狀是「時鐘差幾秒就隨機登不進來」。
LEEWAY_SECONDS = 30

# 契約 §3.2:JWKS 快取 1 小時。
JWKS_TTL_SECONDS = 3600

# 遇未知 kid 時允許提前重抓 JWKS 的最小間隔。
# 🔴 沒有這個下限,任何人送一串亂編的 kid 就能讓本服務去打 IdP(放大攻擊)。
JWKS_REFETCH_MIN_INTERVAL = 60

# discovery 文件本身也快取,避免每次登入都多一次往返。
DISCOVERY_TTL_SECONDS = 3600


class OidcError(Exception):
    """OIDC 相關錯誤,帶 HTTP 狀態碼。

    🔴 契約 §11.5 的分界對登入同樣適用:
       **401 = 憑證無效**(token 壞、過期、簽章不符);
       **403 = 憑證有效但無此權限**(→ 待開通頁,T05)。
       混用會讓查問題的人分不出「是我 token 壞了」還是「我沒被開通」。
       本模組只產生 401/400,403 一律由授權層(T05)發出。
    """

    def __init__(self, status_code: int, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}")
        self.status_code = status_code
        self.code = code
        self.detail = detail


class Transport(Protocol):
    """HTTP 傳輸介面(抽出來是為了讓測試不需要真的 Keycloak)。"""

    def get_json(self, url: str) -> dict: ...

    def post_form(self, url: str, data: dict) -> tuple[int, dict]: ...


class HttpxTransport:
    """正式環境用的傳輸實作。

    副作用: 對外發 HTTP 請求。
    刻意設短 timeout:IdP 掛掉時要快速失敗,不要把本服務的 worker 全部卡住
    ——那會讓一個 IdP 故障變成本服務整體不可用。
    """

    def __init__(self, timeout: float = 5.0) -> None:
        self._timeout = timeout

    def get_json(self, url: str) -> dict:
        import httpx

        r = httpx.get(url, timeout=self._timeout)
        r.raise_for_status()
        return r.json()

    def post_form(self, url: str, data: dict) -> tuple[int, dict]:
        import httpx

        r = httpx.post(url, data=data, timeout=self._timeout)
        try:
            body = r.json()
        except ValueError:
            body = {"error": "non_json_response"}
        return r.status_code, body


# ── PKCE ────────────────────────────────────────────────────────────
def _b64u(raw: bytes) -> str:
    """base64url 無 padding(PKCE 與 at_hash 都是這個編碼)。"""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def new_code_verifier() -> str:
    """產生 PKCE code_verifier(43–128 字元的高熵字串)。

    回傳: str
    副作用: 使用 CSPRNG
    🔴 verifier **只留在伺服器端**;它跟著瀏覽器走就等於沒有 PKCE。
    """
    return _b64u(secrets.token_bytes(32))


def code_challenge_for(verifier: str) -> str:
    """由 verifier 算出 S256 challenge。

    參數: verifier — `new_code_verifier()` 的產物
    回傳: base64url(SHA-256(verifier))
    副作用: 無
    🔴 一律 S256。`plain` 的 challenge 等於把 verifier 直接送出去,
       而它會**照樣登入成功**——退化不會有任何症狀,所以必須有測試釘住。
    """
    return _b64u(hashlib.sha256(verifier.encode("ascii")).digest())


def at_hash_for(access_token: str) -> str:
    """算 OIDC `at_hash`:SHA-256 取左半再 base64url。"""
    digest = hashlib.sha256(access_token.encode("ascii")).digest()
    return _b64u(digest[: len(digest) // 2])


class OidcClient:
    """與 Keycloak 對話的客戶端(單一實例、可重複使用)。

    參數:
      settings  — `app.config.Settings`
      transport — HTTP 傳輸(測試注入替身)
      clock     — 回傳 epoch 秒的可呼叫物(測試可推進)
    副作用: 會對 IdP 發請求並在記憶體快取 discovery / JWKS。
    """

    def __init__(self, settings, *, transport: Transport | None = None, clock=None) -> None:
        self._s = settings
        self._transport: Transport = transport or HttpxTransport()
        self._now = clock or clock_module.now

        # ── realm 由 issuer 推導,不另設一個變數 ──
        # 理由:issuer 是權威(契約 §2.1 discovery 為端點權威來源)。
        # 多一個 `INBOX_OIDC_REALM` 就多一個可以跟 issuer 不一致的地方,
        # 而不一致的症狀是「JWKS 抓到別的 realm 的鑰」= 全部驗不過。
        self._issuer = settings.oidc_issuer.rstrip("/")
        head, sep, realm = self._issuer.rpartition("/realms/")
        if not sep:
            raise ValueError(
                f"INBOX_OIDC_ISSUER 形狀不對(應含 /realms/<realm>):{settings.oidc_issuer}"
            )
        self._realm = realm
        self._internal_realm_base = f"{settings.oidc_internal_base.rstrip('/')}/realms/{realm}"

        self._discovery: dict | None = None
        self._discovery_at = 0.0
        self._jwks: dict[str, Any] = {}
        self._jwks_at = 0.0
        self._last_refetch = 0.0        # 上次「因未知 kid」提前重抓的時刻
        self._missing_kids: set[str] = set()

    # ── 位址處理(契約 §2.4)────────────────────────────────────────
    def _to_internal(self, url: str) -> str:
        """把端點位址換成容器內可達的位址(給伺服器自己連的:token / JWKS)。

        🔴 為什麼要換:Keycloak 的 discovery 會依 `KC_HOSTNAME` 回**對外**位址,
        而本容器連對外位址得繞出去再繞回來(經 gateway),多一層失敗點;
        有些網路設定下根本不通,而症狀是「登入到一半卡住」。
        """
        return self._swap_base(url, self._internal_realm_base)

    def _to_external(self, url: str) -> str:
        """把端點位址換成對外位址(給**瀏覽器**去的:authorize / logout)。

        🔴 反過來一樣要換:內部位址(`http://keycloak:8080/...`)送到瀏覽器
        會直接連不上,而使用者看到的是空白頁,自家 log 什麼都沒有。
        """
        return self._swap_base(url, self._issuer)

    def _swap_base(self, url: str, to_base: str) -> str:
        """把 url 的 realm base 換成 to_base;認不出來就原樣回傳。

        認不出來時不亂改——discovery 是端點的權威來源(契約 §2.1),
        猜錯比原樣送出更難查。
        """
        for base in (self._issuer, self._internal_realm_base):
            if url.startswith(base):
                return to_base + url[len(base) :]
        return url

    # ── discovery ─────────────────────────────────────────────────
    def discovery(self) -> dict:
        """取 discovery 文件(快取 1h)。

        回傳: dict
        副作用: 可能對 IdP 發一次 GET
        """
        if self._discovery is not None and self._now() - self._discovery_at < DISCOVERY_TTL_SECONDS:
            return self._discovery
        url = f"{self._internal_realm_base}/.well-known/openid-configuration"
        try:
            doc = self._transport.get_json(url)
        except Exception as exc:  # 網路/DNS/5xx 一律歸為 IdP 不可用
            raise OidcError(503, "idp_unavailable", f"discovery 取用失敗:{type(exc).__name__}")
        # 契約 §2.4:discovery 回報的 issuer 必須與我方設定**逐字相同**,
        # 否則之後每一個 token 的 iss 都驗不過,而錯誤訊息只說「token 無效」。
        if doc.get("issuer", "").rstrip("/") != self._issuer:
            raise OidcError(
                503,
                "issuer_mismatch",
                f"discovery 的 issuer={doc.get('issuer')!r} 與設定 {self._issuer!r} 不符",
            )
        self._discovery = doc
        self._discovery_at = self._now()
        return doc

    # ── 授權導向 ───────────────────────────────────────────────────
    @property
    def redirect_uri(self) -> str:
        """本服務的 callback 位址。

        🔴 **必須逐字等於 client 的登記值**,含結尾斜線:
           `https://catsapp.sporton.com.tw/inbox/oidc/callback/`

        契約 v2.14 的 PLM 事故:它用 Django `reverse()` 動態組,而 `reverse()`
        回的是**後註冊**的那個掛載點,實際送出的值與登記值不同 → 首次登入即
        mismatch,**錯誤停在 Keycloak 的頁面、PLM 自己的 log 是空的**。
        所以這裡刻意用字串組合而非任何「反查路由」的機制,並以測試逐字釘住。
        """
        base = self._s.public_base_url.rstrip("/")
        return f"{base}{self._s.base_path}/oidc/callback/"

    def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        """組出要把瀏覽器導去的授權端點 URL。

        參數: state / nonce — 一次性隨機值;code_challenge — S256 challenge
        回傳: 完整 URL(對外位址)
        副作用: 可能觸發一次 discovery
        """
        endpoint = self._to_external(self.discovery()["authorization_endpoint"])
        query = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": self._s.oidc_client_id,
                "redirect_uri": self.redirect_uri,
                # 刻意只要 openid:本服務不申請 email(通知一律站內顯示),
                # 也不申請 groups——portal 已從 client 預設 scope 移除 email,
                # 這裡再多要一次會讓「不申請」變成口號。
                "scope": "openid",
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{endpoint}?{query}"

    # ── token 端點 ────────────────────────────────────────────────
    def exchange_code(self, *, code: str, code_verifier: str) -> dict:
        """用授權碼換 token。

        參數: code — IdP 回傳的一次性授權碼;code_verifier — 對應的 PKCE verifier
        回傳: token response(access_token / refresh_token / id_token / expires_in)
        副作用: 對 token 端點發一次 POST
        錯誤: 授權碼失效/重放 → OidcError(400, "invalid_grant")

        💡 契約 v2.17 的用法:拿一個**必定失敗**的 code 打這裡,
           回 `invalid_grant` 就證明 client secret 是對的(secret 錯會回
           `invalid_client`)——不需真人登入、不需 gateway 路由。
        """
        return self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "code_verifier": code_verifier,
            }
        )

    def refresh(self, refresh_token: str) -> dict:
        """用 refresh_token 換一組新的 token。

        參數: refresh_token
        回傳: 新的 token response
        副作用: 對 token 端點發一次 POST
        錯誤: IdP 已停用帳號/session 結束 → OidcError(400, "invalid_grant")

        🔴 這是契約 §3.3 換來的「收權即時性」:access token 只有 300 秒,
           帳號被停用後最多再活 300 秒,因為下一次 refresh 就會失敗。
           呼叫端(routes_auth)必須把失敗當成「登出」,沿用舊 session
           會讓收權變成假的。
        """
        return self._token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})

    def _token_request(self, form: dict) -> dict:
        """對 token 端點發請求並統一錯誤形狀。

        副作用: 一次 HTTP POST。
        🔴 client_secret 只出現在這裡、只進 POST body,不進 URL、不進 log。
        """
        endpoint = self._to_internal(self.discovery()["token_endpoint"])
        payload = dict(form)
        payload["client_id"] = self._s.oidc_client_id
        payload["client_secret"] = self._s.oidc_client_secret
        try:
            status, body = self._transport.post_form(endpoint, payload)
        except Exception as exc:
            raise OidcError(503, "idp_unavailable", f"token 端點失敗:{type(exc).__name__}")
        if status != 200:
            # 只帶 IdP 的錯誤代碼,不帶 description(可能含使用者資訊)
            raise OidcError(400, body.get("error", "token_request_failed"), f"HTTP {status}")
        return body

    # ── JWKS 與驗證 ───────────────────────────────────────────────
    def _fetch_jwks(self) -> None:
        """抓一次 JWKS 並重建快取。

        副作用: 一次 HTTP GET;覆寫 `self._jwks`。
        成功抓到就清掉「已知找不到的 kid」——輪替後那些 kid 可能已經存在了。
        """
        url = self._to_internal(self.discovery()["jwks_uri"])
        try:
            doc = self._transport.get_json(url)
        except Exception as exc:
            raise OidcError(503, "idp_unavailable", f"JWKS 取用失敗:{type(exc).__name__}")
        keys: dict[str, Any] = {}
        for jwk in doc.get("keys", []):
            if jwk.get("kty") != "RSA" or jwk.get("use") not in (None, "sig"):
                continue
            kid = jwk.get("kid")
            if not kid:
                continue
            try:
                keys[kid] = jwt.PyJWK(jwk, algorithm="RS256").key
            except Exception:
                # 單一把鑰壞掉不該讓整份 JWKS 失效(輪替期常有半套設定)
                continue
        self._jwks = keys
        self._jwks_at = self._now()
        self._missing_kids.clear()

    def _key_for(self, kid: str | None):
        """依 kid 取公鑰,必要時重抓 JWKS。

        參數: kid — token header 的 kid
        回傳: 公鑰物件
        錯誤: 找不到 → OidcError(401)

        🔴 三段邏輯各自對應一個真實故障:
          ① 快取過期(1h)→ 重抓:不然金鑰輪替後**全部人登不進來**;
          ② 未知 kid → 立刻重抓一次:輪替當天新鑰出現在舊快取之外;
          ③ 已知找不到 + 未達最小間隔 → 不重抓:否則亂編 kid 就能讓本服務去打 IdP。
        """
        if not kid:
            raise OidcError(401, "invalid_token", "token header 無 kid")

        # ① 快取空或已過 1h → 依 TTL 重抓
        if not self._jwks or self._now() - self._jwks_at >= JWKS_TTL_SECONDS:
            self._fetch_jwks()

        key = self._jwks.get(kid)
        if key is not None:
            return key

        # ② 未知 kid → 提前重抓一次(輪替當天新鑰會出現在舊快取之外)
        # ③ 但同一個 kid 不再重試,且兩次提前重抓之間至少隔
        #    JWKS_REFETCH_MIN_INTERVAL 秒——否則亂編一串 kid 就能讓本服務去打 IdP
        cooled = self._now() - self._last_refetch >= JWKS_REFETCH_MIN_INTERVAL
        if kid not in self._missing_kids and cooled:
            self._last_refetch = self._now()
            self._fetch_jwks()
            key = self._jwks.get(kid)
            if key is not None:
                return key

        if len(self._missing_kids) > 64:
            # 上限:別讓亂編的 kid 把記憶體吃掉(清空只是讓下一次重試一遍,無害)
            self._missing_kids.clear()
        self._missing_kids.add(kid)
        raise OidcError(401, "invalid_token", "JWKS 找不到對應的 kid")

    def verify_id_token(
        self, token: str, *, nonce: str | None = None, access_token: str | None = None
    ) -> dict:
        """驗證 id_token 並回傳 claims。

        參數:
          token        — id_token(JWT)
          nonce        — 登入時發出的一次性值;傳入即比對(refresh 換來的
                         id_token 依規範不帶 nonce,故該情境不傳)
          access_token — 同一次 token response 的 access token;有傳才比 `at_hash`
        回傳: claims dict
        錯誤: 任何驗證不過一律 OidcError(401)——不細分原因給呼叫方,
              細節只進 log(避免把「哪一項不對」告訴攻擊者)
        副作用: 可能觸發 JWKS 取用
        """
        try:
            header = jwt.get_unverified_header(token)
        except Exception:
            raise OidcError(401, "invalid_token", "無法解析 token header")

        alg = header.get("alg")
        if alg != "RS256":
            # 契約 §3.2:只接受 RS256。HS256 是對稱簽章——知道 client secret
            # 的人就能自簽身分;`none` 連簽章都沒有。兩者都必須在驗簽**之前**擋掉。
            raise OidcError(401, "invalid_token", f"演算法 {alg!r} 不被接受(僅 RS256)")

        key = self._key_for(header.get("kid"))

        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=self._s.oidc_client_id,
                issuer=self._issuer,
                options={
                    # exp/iat 自己驗:PyJWT 內部用 time.time(),沒有注入時鐘的地方,
                    # 而 ±30s 容忍與「300 秒後續期」正是只在時間邊界出錯的兩條。
                    "verify_exp": False,
                    "verify_iat": False,
                    "require": ["iss", "aud", "sub", "exp", "iat"],
                },
            )
        except jwt.InvalidAudienceError:
            raise OidcError(401, "invalid_token", "aud 不是本 client")
        except jwt.InvalidIssuerError:
            raise OidcError(401, "invalid_token", "iss 不符")
        except jwt.MissingRequiredClaimError as exc:
            raise OidcError(401, "invalid_token", f"缺必要 claim:{exc.claim}")
        except jwt.InvalidTokenError as exc:
            raise OidcError(401, "invalid_token", f"驗簽失敗:{type(exc).__name__}")
        except Exception as exc:
            # 🔴 任何**未預期**的解析錯誤也一律 401,不得漏成 500。
            # 這道網是實測補上的:把 alg 白名單放寬到 HS256 後,PyJWT 因為
            # 「不准拿非對稱金鑰做 HMAC」而丟出 `TypeError`——不是
            # `InvalidTokenError`,於是會變成 500。500 的代價有兩層:
            # 它告訴攻擊者他找到了未處理路徑,也會在半夜把人叫起來。
            raise OidcError(401, "invalid_token", f"驗證失敗:{type(exc).__name__}")

        self._verify_time(claims)

        if nonce is not None and claims.get("nonce") != nonce:
            # nonce 對不上 = 這個 id_token 不是本次登入換來的(重放)
            raise OidcError(401, "invalid_token", "nonce 不符")

        self._verify_at_hash(claims, access_token)
        return claims

    def _verify_time(self, claims: dict) -> None:
        """驗 exp(必)與 iat(容忍未來 30 秒),±30 秒容忍。

        契約 §3.3:±30s 是**驗證方**的義務,Keycloak realm 沒有對應開關。
        """
        now = self._now()
        exp = claims.get("exp")
        if exp is None or now - LEEWAY_SECONDS > float(exp):
            raise OidcError(401, "invalid_token", "token 已過期")
        iat = claims.get("iat")
        if iat is not None and float(iat) - LEEWAY_SECONDS > now:
            # 簽發時間在未來 30 秒之外 → 對方時鐘不對(契約 v2.16 記載過
            # 「時鐘快兩天」的實例,症狀只說 token 無效,指不出是時鐘)
            raise OidcError(401, "invalid_token", "iat 在未來(時鐘不同步?)")

    def _verify_at_hash(self, claims: dict, access_token: str | None) -> None:
        """驗 `at_hash`(若 claim 存在且我方持有 access_token)。

        🔴 這個方法的存在理由是契約 v3.2 記載的 PLM 事故:
           開旗標當天**第一個真人登入 100% 失敗**——
           `No access_token provided to compare against at_hash claim.`
           Keycloak 的 id_token 帶 `at_hash`,而 PLM 的驗證呼叫沒有地方傳 access_token,
           **它四百多支離線測試一支都沒抓到,因為測試自造的 token 沒有那個 claim。**

        本專案的明確選擇(二者擇一,並以測試釘住):
          - claim 存在 + 有 access_token → **必須相符**,不符即 401
            (帶了不驗等於沒帶,它防的是「拿 A 的 id_token 配 B 的 access token」)
          - claim 存在 + 沒有 access_token → **跳過,不拋錯**
            (PLM 炸的就是這條路徑;`at_hash` 對授權碼流程是選用的,
             真正的保護來自 code + PKCE + nonce)
          - claim 不存在 → 跳過(符合 OIDC 規範,不得因此拒絕)
        """
        expected = claims.get("at_hash")
        if not expected or access_token is None:
            return
        if at_hash_for(access_token) != expected:
            raise OidcError(401, "invalid_token", "at_hash 與 access_token 不符")

    # ── 登出(T06 會用到,先把端點取出來)─────────────────────────
    def end_session_url(self, *, id_token_hint: str | None, post_logout_redirect: str) -> str:
        """組出 IdP 的登出 URL。

        參數: id_token_hint — 供 IdP 認出要結束哪個 session;post_logout_redirect — 登出後回哪
        回傳: 對外 URL
        副作用: 可能觸發 discovery
        """
        endpoint = self._to_external(self.discovery()["end_session_endpoint"])
        params: dict[str, str] = {
            "post_logout_redirect_uri": post_logout_redirect,
            "client_id": self._s.oidc_client_id,
        }
        if id_token_hint:
            params["id_token_hint"] = id_token_hint
        return f"{endpoint}?{urllib.parse.urlencode(params)}"


def log_event(event: str, **fields: Any) -> None:
    """單行 JSON log(共通紅線:走 stdout、不記密碼/完整 token/個資)。

    參數: event — 事件名;fields — 附帶欄位
    副作用: 印到 stdout
    🔴 本服務紅線:訊息主旨與內容不進 log;這裡也不得放 token 全文、
       不得放使用者姓名或 email。`sub` 是允許的(它就是本地身分鍵)。
    """
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)
