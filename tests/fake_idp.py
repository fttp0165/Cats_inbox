# -*- coding: utf-8 -*-
"""測試用的 Keycloak 替身(簽發 id_token、提供 JWKS)。

用途: 讓 T04 的驗證邏輯能在**沒有真 Keycloak、沒有 secret** 的情況下被測到。
副作用: 無網路、無檔案;RSA 金鑰在記憶體產生。

🔴 為什麼這支替身要刻意「像真的一樣囉嗦」:

契約 v3.2 記載的 PLM 事故——開旗標當天**第一個真人登入 100% 失敗**於
`at_hash`,而 PLM 的**四百多支離線測試一支都沒抓到**。根因不是少寫測試,
是**測試自己造的 token 沒有那個 claim**:真實 IdP 多給一個 claim,
而那個 claim 改變了函式庫的行為。

所以本替身的預設行為是「**給滿真實 Keycloak 會給的 claims**」
(`at_hash`/`sid`/`azp`/`nonce`/`typ`/`iat`/`auth_time`),
要少給必須**明確傳參**。預設寬鬆的替身會把它想保護的缺陷一起抹平。

同理 `exp`:替身簽出的 token **真的會過期**(以呼叫方給的時鐘為準),
永不過期的假 token 會讓「±30s 容忍」與「主動 refresh」兩條測試都變成裝飾。
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

# 對外身分:與 portal 已建立的 client 一致(`idp/bootstrap/client-cats-inbox.sh`)
ISSUER = "https://catsapp.sporton.com.tw/auth/realms/sporton"
CLIENT_ID = "cats-inbox"
# 🔴 client 登記的 redirect URI,**帶結尾斜線**。逐字比對用,不得「差不多」。
REGISTERED_REDIRECT_URI = "https://catsapp.sporton.com.tw/inbox/oidc/callback/"


def _b64u(raw: bytes) -> str:
    """base64url 無 padding 編碼(JWK 與 at_hash 都用這個形式)。"""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _int_to_b64u(value: int) -> str:
    """把 RSA 的大整數轉成 JWK 的 base64url 位元組串。"""
    length = (value.bit_length() + 7) // 8
    return _b64u(value.to_bytes(length, "big"))


def at_hash_for(access_token: str) -> str:
    """算 OIDC 的 `at_hash`:SHA-256 取**左半**再 base64url。

    參數: access_token — 同一次 token response 裡的 access token
    回傳: at_hash 字串
    副作用: 無
    """
    digest = hashlib.sha256(access_token.encode("ascii")).digest()
    return _b64u(digest[: len(digest) // 2])


@dataclass
class FakeKey:
    """一把 RSA 金鑰 + 它的 `kid`(JWKS 輪替測試需要兩把)。"""

    kid: str
    private: rsa.RSAPrivateKey

    @property
    def jwk(self) -> dict:
        """回傳這把鑰的公開 JWK(Keycloak `/certs` 端點的形狀)。"""
        numbers = self.private.public_key().public_numbers()
        return {
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": self.kid,
            "n": _int_to_b64u(numbers.n),
            "e": _int_to_b64u(numbers.e),
        }

    @property
    def pem(self) -> bytes:
        """私鑰 PEM(只給 `jwt.encode` 用)。"""
        from cryptography.hazmat.primitives import serialization

        return self.private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )


@dataclass
class FakeIdP:
    """Keycloak 替身。

    屬性:
      keys — 至少兩把(模擬金鑰輪替期新舊並存)
      jwks_calls — JWKS 端點被取用次數;快取與輪替測試靠它斷言
    """

    keys: list[FakeKey] = field(default_factory=list)
    jwks_calls: int = 0

    @classmethod
    def build(cls, kids: tuple[str, ...] = ("kid-old", "kid-new")) -> "FakeIdP":
        """產生一個帶 N 把鑰的替身。

        參數: kids — 各把鑰的 kid
        回傳: FakeIdP
        副作用: 產生 RSA 金鑰(耗時,測試以 module 級 fixture 共用)
        """
        keys = [
            FakeKey(kid=k, private=rsa.generate_private_key(public_exponent=65537, key_size=2048))
            for k in kids
        ]
        return cls(keys=keys)

    def key(self, kid: str) -> FakeKey:
        """依 kid 取鑰;找不到即測試自己寫錯,直接炸。"""
        for k in self.keys:
            if k.kid == kid:
                return k
        raise KeyError(kid)

    # ── 端點替身 ────────────────────────────────────────────────
    def jwks(self, only: tuple[str, ...] | None = None) -> dict:
        """JWKS 端點回應。

        參數: only — 只公開這些 kid(模擬「輪替前舊鑰還在 / 新鑰尚未公開」)
        回傳: {"keys": [...]}
        副作用: `jwks_calls += 1`(這就是「有沒有真的去抓」的證據)
        """
        self.jwks_calls += 1
        keys = [k for k in self.keys if only is None or k.kid in only]
        return {"keys": [k.jwk for k in keys]}

    def discovery(self) -> dict:
        """OIDC discovery 文件(契約 §2.1:discovery 是端點的權威來源)。

        🔴 `issuer` 一律是**對外**網址,即使容器內是走內部位址抓的
        ——契約 §2.4:改 `iss` 會讓所有 token 驗不過。
        """
        internal = "http://keycloak:8080/auth/realms/sporton"
        return {
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/protocol/openid-connect/auth",
            "token_endpoint": f"{internal}/protocol/openid-connect/token",
            "jwks_uri": f"{internal}/protocol/openid-connect/certs",
            "end_session_endpoint": f"{ISSUER}/protocol/openid-connect/logout",
        }

    # ── 簽 token ────────────────────────────────────────────────
    def id_token(
        self,
        *,
        now: float,
        kid: str = "kid-new",
        sub: str = "11111111-2222-3333-4444-555555555555",
        aud: str | None = None,
        iss: str | None = None,
        nonce: str | None = "test-nonce",
        expires_in: int = 300,
        access_token: str | None = "fake-access-token",
        alg: str = "RS256",
        omit: tuple[str, ...] = (),
        extra: dict | None = None,
    ) -> str:
        """簽一個 id_token。

        參數:
          now         — 簽發時刻(epoch 秒);測試自己控制時鐘
          expires_in  — 幾秒後過期(可為負數 = 已過期)
          access_token— 用來算 `at_hash`;None 則不放 at_hash
          alg         — 故意簽錯演算法用("HS256" / "none")
          omit        — 刻意拿掉的 claim 名(測「少一個 claim 會怎樣」)
        回傳: 已簽名的 JWT 字串
        副作用: 無

        🔴 預設**給滿** Keycloak 實際會給的 claims,理由見本檔檔頭。
        """
        claims = {
            "iss": iss or ISSUER,
            "aud": aud if aud is not None else CLIENT_ID,
            "azp": CLIENT_ID,
            "sub": sub,
            "typ": "ID",
            "iat": int(now),
            "auth_time": int(now),
            "exp": int(now) + expires_in,
            "sid": "sid-abc-123",
            "preferred_username": "tester",
            "email_verified": True,
        }
        if nonce is not None:
            # nonce 只出現在授權碼流程換來的 id_token;refresh 換來的不帶
            claims["nonce"] = nonce
        if access_token is not None:
            claims["at_hash"] = at_hash_for(access_token)
        for name in omit:
            claims.pop(name, None)
        if extra:
            claims.update(extra)

        # 🔴 簽錯演算法的假 token **一律帶上真實存在的 `kid`**。
        #    這不是細節:演算法混淆攻擊(alg confusion)的標準做法就是
        #    從一個真 token 抄走 header 的 kid,只把 alg 換掉。
        #    不帶 kid 的假 token 會在「找不到 kid」那一關就被擋下來,
        #    於是「我方有沒有擋 alg」這件事**根本沒被測到**——
        #    測試會綠,但綠的理由不是它宣稱的那個。
        if alg == "none":
            # PyJWT 不讓你輕鬆簽 alg=none,手工組:這才是攻擊者實際會送的形狀
            header = _b64u(json.dumps({"alg": "none", "typ": "JWT", "kid": kid}).encode())
            body = _b64u(json.dumps(claims).encode())
            return f"{header}.{body}."
        if alg == "HS256":
            # 對稱簽章:任何知道 client secret 的人都能自簽身分 → 契約 §3.2 禁用
            return jwt.encode(
                claims, "not-really-a-secret", algorithm="HS256", headers={"kid": kid}
            )

        k = self.key(kid)
        return jwt.encode(claims, k.pem, algorithm="RS256", headers={"kid": k.kid})
