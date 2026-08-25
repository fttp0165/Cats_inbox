# -*- coding: utf-8 -*-
"""伺服器端 session(cookie 只放一個不可猜的索引鍵)。

用途: 保存登入狀態與 token,讓瀏覽器只拿到一個索引,不拿到任何 token。
副作用: 在行程記憶體保存 session;不寫 DB、不寫檔。

🔴 為什麼是「伺服器端 session」而不是「把 token 塞進簽章 cookie」——兩個硬理由:

1. **T06 的 front-channel logout 做不到。** 契約 §10.3 要求本服務提供
   `GET /inbox/oidc/frontchannel-logout`,由 **IdP 主動呼叫**、帶 `sid`。
   那一刻**使用者的瀏覽器不在現場**,我方只有一個 `sid` 可以用來找 session。
   無狀態 cookie 是「只有瀏覽器有」的東西——**IdP 通知得到,我方卻無從作廢**,
   而症狀是「入口登出了,但 inbox 還是登入狀態」(契約 §10.7 的實例)。
2. **契約 §4.10 同源義務**的精神是 token 不落到前端。refresh_token 尤其:
   它換得出新的 access token,壽命比 access token 長得多。

代價(誠實標註,不假裝沒有):
  - **重啟即全員重新登入**。本服務刻意不裝 Redis(A.1「刻意極簡」),
    重啟後 session 全失。使用者的體感是多一次跳轉(SSO 已登入 → 免輸密碼),
    可接受;但這是**已知限制**,不是沒想到。
  - 單一容器有效。要開副本必須先把 session 搬到共用儲存(列 T04 遺留)。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from itsdangerous import BadSignature, URLSafeSerializer

from app import clock as clock_module

# cookie 名稱。前綴統一,方便在瀏覽器裡一眼分辨是誰的。
SESSION_COOKIE = "inbox_session"
TX_COOKIE = "inbox_oidc_tx"

# access token 只剩這麼多秒時就主動續期(契約 §3.3 的 300 秒陷阱)。
# 取 60 秒:比一次頁面請求的耗時大兩個數量級,又遠小於 300。
REFRESH_MARGIN_SECONDS = 60

# 登入交易(state/nonce/verifier)的存活時間。授權碼本身只有 60 秒,
# 但使用者可能在 IdP 頁面上停留(輸密碼、二階段驗證),故給 10 分鐘。
TX_TTL_SECONDS = 600


@dataclass
class SessionData:
    """一個已登入使用者的 session。

    🔴 刻意**只存 `sub`**,不存姓名、不存 email(平台紅線:業務庫只存 sub)。
    `claims` 只保留驗證後需要的少數欄位,不整包塞。
    """

    sub: str
    idp_sid: str | None          # IdP 的 session id;front-channel logout 靠它找人
    access_token: str
    refresh_token: str | None
    id_token: str                # 登出時當 id_token_hint 用(T06)
    access_expires_at: float
    created_at: float


@dataclass
class PendingLogin:
    """一次尚未完成的登入(state / nonce / PKCE verifier)。"""

    state: str
    nonce: str
    code_verifier: str
    created_at: float


class SessionStore:
    """記憶體 session 儲存 + cookie 值簽章。

    參數: secret — 簽 cookie 用(env `INBOX_SESSION_SECRET`);clock — 可注入時鐘
    副作用: 持有 session 字典。

    簽章的用途不是保密(cookie 裡只有一個隨機索引,本身無意義),而是:
      ① 被竄改的 cookie 在查表之前就被擋掉;
      ② **換掉 secret 等於一次登出所有人**——這是有實際用途的維運手段。
    """

    def __init__(self, secret: str, *, clock=None) -> None:
        if not secret:
            # fail-closed:沒有 secret 就不要假裝有 session(紅線:secret 無預設值)
            raise ValueError("INBOX_SESSION_SECRET 未設定,無法建立 session 儲存")
        self._serializer = URLSafeSerializer(secret, salt="cats-inbox-session")
        # CSRF token 用的原始 secret。🔴 **不能沿用 `URLSafeSerializer`** ——
        # 它是**簽章不是加密**,token 會把 session key 明文(base64)帶到 HTML 上,
        # 而那正是密封 cookie 在保護的東西。所以走 HMAC(單向)。
        self._secret = secret
        self._now = clock or clock_module.now
        self._sessions: dict[str, SessionData] = {}
        self._pending: dict[str, PendingLogin] = {}

    # ── cookie 值 ─────────────────────────────────────────────────
    def seal(self, key: str) -> str:
        """把索引鍵簽成 cookie 值。"""
        return self._serializer.dumps(key)

    def unseal(self, cookie_value: str | None) -> str | None:
        """驗簽並取出索引鍵;簽章不符或缺值回 None。"""
        if not cookie_value:
            return None
        try:
            return self._serializer.loads(cookie_value)
        except BadSignature:
            return None

    # ── CSRF(T09b:本專案第一個 POST 表單)────────────────────────
    def csrf_token(self, key: str) -> str:
        """為某個 session 產生 CSRF token。

        參數: key — session 的索引鍵(不是 cookie 值)
        回傳: 十六進位字串
        副作用: 無(純計算,不存任何東西)

        🔴 **HMAC(單向),不是簽章。** 用 `URLSafeSerializer` 之類的簽章做 token
           會把 `key` 明文帶到 HTML 上 —— 而 `key` 是密封 cookie 保護的內層值。
           token 會被算繪到頁面上、可能落在 referer / 快取 / 螢幕截圖裡。

        🔴 **綁 session。** token 綁在 `key` 上,所以攻擊者在自己 session 拿到的
           合法 token 拿去打受害者的請求會失敗。只驗「簽章對不對」而不綁 session
           的實作會通過所有「有 token 就放行」的測試,卻完全擋不住 CSRF。

        ⚠ 不另存狀態:token 由 `key` 推導,所以 session 一旦輪替(重新登入、
           登出、換 secret),舊 token 自動失效 —— 不需要另一份會忘記清理的表。
        """
        return hmac.new(
            self._secret.encode("utf-8"), f"csrf:{key}".encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def verify_csrf(self, key: str, token: str | None) -> bool:
        """比對 CSRF token(定值時間比較)。

        回傳: 相符為 True
        ⚠ 用 `hmac.compare_digest` 而不是 `==`:字串比較會提早結束,
          而那個時間差理論上可被用來逐字元猜 token。
        """
        if not token:
            return False
        return hmac.compare_digest(self.csrf_token(key), token)

    # ── 登入交易 ──────────────────────────────────────────────────
    def start_login(self, pending: PendingLogin) -> str:
        """存下一次登入交易,回傳它的索引鍵。"""
        key = secrets.token_urlsafe(32)
        self._pending[key] = pending
        self._sweep_pending()
        return key

    def take_login(self, key: str | None) -> PendingLogin | None:
        """取出並**刪除**登入交易(一次性:同一個 state 不得用兩次)。"""
        if not key:
            return None
        pending = self._pending.pop(key, None)
        if pending is None:
            return None
        if self._now() - pending.created_at > TX_TTL_SECONDS:
            return None
        return pending

    def _sweep_pending(self) -> None:
        """清掉逾時的登入交易(使用者按了登入卻沒登完,很常見)。"""
        deadline = self._now() - TX_TTL_SECONDS
        for k in [k for k, v in self._pending.items() if v.created_at < deadline]:
            self._pending.pop(k, None)

    # ── session ───────────────────────────────────────────────────
    def create(self, data: SessionData) -> str:
        """建立 session,回傳索引鍵。"""
        key = secrets.token_urlsafe(32)
        self._sessions[key] = data
        return key

    def get(self, key: str | None) -> SessionData | None:
        """取 session;不存在回 None。"""
        return self._sessions.get(key) if key else None

    def replace(self, key: str, data: SessionData) -> None:
        """就地更新 session(續期後換新 token 用)。

        🔴 刻意**不換索引鍵**:換鍵就得重設 cookie,而續期發生在任何一個
        普通 GET 上——包含瀏覽器的預抓與並行請求。換鍵會讓其中一個請求
        拿著剛被作廢的鍵,症狀是「隨機掉登入」。
        """
        if key in self._sessions:
            self._sessions[key] = data

    def delete(self, key: str | None) -> None:
        """刪除 session(自發登出)。"""
        if key:
            self._sessions.pop(key, None)

    def delete_by_idp_sid(self, idp_sid: str) -> int:
        """刪除某個 IdP session 對應的所有本地 session(T06 front-channel)。

        參數: idp_sid — IdP 送來的 `sid`
        回傳: 實際刪掉幾個(0 也是正常結果——冪等)
        副作用: 移除 session
        """
        keys = [k for k, v in self._sessions.items() if v.idp_sid == idp_sid]
        for k in keys:
            self._sessions.pop(k, None)
        return len(keys)

    def cookie_kwargs(self, base_path: str) -> dict:
        """統一的 cookie 屬性(契約 §4.10)。

        回傳: 給 `set_cookie` 的關鍵字參數
        - `path` 限定在自己的子路徑:同一個 hostname 之下還有其他 App,
          不設 Path 會把本服務的 cookie 送給它們(D2″ 單一 hostname 的直接後果)。
        - `httponly`:JS 讀不到。
        - `samesite=lax`:OIDC 是 top-level 導覽回來的 GET,lax 會帶上 cookie;
          strict 會讓 callback 收不到交易 cookie,而症狀是「登入後又要你登入」。
        - `secure`:只走 HTTPS(正式站 gateway 已強制 HTTPS)。
        """
        path = base_path if base_path.endswith("/") else base_path + "/"
        return {"path": path, "httponly": True, "samesite": "lax", "secure": True}
