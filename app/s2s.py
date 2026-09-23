# -*- coding: utf-8 -*-
"""服務對服務(S2S)的認證與授權(T14a):**誰在呼叫、能不能做這件事**。

用途: 讓推送端點(以及日後任何 S2S 端點)用一行 `Depends(...)` 拿到「已驗證的呼叫方」。
副作用: 可能觸發 JWKS 取用(經 `OidcClient`);讀資料庫查來源登記表;拒絕時寫一行 log。

契約 §11.5 資源方三條義務,在這裡各有一個落點:

| 義務 | 落點 | 不過時 |
|---|---|---|
| ① 驗 `aud` = 自己的 client_id | `OidcClient.verify_access_token` | **401** |
| ② **逐端點**驗 scope | `require_s2s_scope(scope)` —— scope 是**參數**,每個端點自己宣告 | **403** `insufficient_scope` |
| ③ deny-by-default、「**內網不是身分**」 | 查 `source_app` 登記表;本模組**完全不看來源位址** | **403** |

🔴 **401 與 403 必須分開**(§11.5):
   401 = 憑證無效(沒帶、壞、過期、**不是服務憑證**)→ 呼叫方該換 token;
   403 = 憑證有效但無此權限(缺 scope、未登記、已停用)→ 呼叫方該找人開通。
   混用的話,呼叫方查不出是哪一種,而兩者的下一步完全不同。

🔴 **本模組不讀 session cookie。** 瀏覽器的登入 session 不是服務憑證;
   一個已登入的使用者對推送端點發請求,拿到的必須與沒登入的人一樣(401)。
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from app.db import session_scope
from app.oidc import OidcError, log_event

# 推送端點要求的 scope(契約 §11:`<資源>:<動作>`,不繼承)。
# 🔴 **寫死,不從設定讀** —— 與 `ROLE_CAPABILITIES`、`SITE_ORIGIN` 同一個理由:
#    設定可以被改寬,而改寬不會有任何症狀(推送照樣成功,只是誰都能推)。
SCOPE_NOTIFICATION_PUSH = "notification:push"

# `WWW-Authenticate` 的 realm 值(RFC 6750 §3)
REALM = "cats-inbox"


@dataclass(frozen=True)
class S2SCaller:
    """一個**已通過認證與授權**的服務呼叫方。

    屬性:
      azp        — 呼叫方的 client_id(登記表的鍵)
      sub        — 呼叫方 service account 使用者的 sub(`X-User-Id` 不得等於它)
      source_app — 登記表上的顯示標籤(**唯一**會寫進 `message.source_app` 的值)
    """

    azp: str
    sub: str
    source_app: str


def _challenge(error: str | None = None, *, scope: str | None = None) -> dict:
    """組 `WWW-Authenticate` 標頭(RFC 6750 §3)。

    ⚠ 呼叫方是程式,不是人:它靠這個標頭區分「換一張 token 再試」(`invalid_token`)
      與「去申請 scope」(`insufficient_scope`)。只給狀態碼的話,它只能猜。
    """
    parts = [f'realm="{REALM}"']
    if error:
        parts.append(f'error="{error}"')
    if scope:
        parts.append(f'scope="{scope}"')
    return {"WWW-Authenticate": "Bearer " + ", ".join(parts)}


def _reject(request: Request, status: int, code: str, detail: str, *,
            azp: str | None = None, scope: str | None = None) -> OidcError:
    """記一行拒絕 log,並回傳要拋的例外。

    副作用: 寫一行 log(`s2s_rejected`)
    🔴 log 只有**原因代碼、細節、`azp`、路徑** —— 不記 token(共通紅線:
       不記完整 token),也不記請求內容(本服務紅線)。
       拒絕**一定要留痕**:「對方說推了、我們這邊什麼都沒有」時,
       這一行是唯一能說出「是哪一關擋的」的東西。
    ⚠ 細節(`detail`)只進 log,**不**回給呼叫方 —— 回給呼叫方的只有代碼
      (`OidcError` 的慣例:把「哪一項不對」告訴攻擊者等於幫他縮小範圍)。
    """
    log_event("s2s_rejected", reason=code, detail=detail, azp=azp, path=request.url.path)
    if status == 401:
        headers = _challenge(None if code == "missing_token" else "invalid_token")
    elif code == "insufficient_scope":
        headers = _challenge("insufficient_scope", scope=scope)
    else:
        headers = None
    return OidcError(status, code, detail, headers=headers)


def _bearer_token(request: Request) -> str | None:
    """自 `Authorization` 標頭取 Bearer token;沒有或不是 Bearer → None。

    ⚠ scheme 不分大小寫(RFC 7235 §2.1),但**只接受 Bearer** ——
      `Basic` 之類的憑證在本端點一律視為「沒帶服務憑證」。
    """
    value = request.headers.get("authorization", "")
    scheme, _, token = value.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def require_s2s_scope(scope: str, *, own_client_id: str):
    """產生一個「要求服務憑證 + 某個 scope + 已登記來源」的 FastAPI 相依。

    參數:
      scope         — 本端點要求的 scope(**每個端點自己宣告**:這就是「逐端點驗」)
      own_client_id — 我方自己的 client_id(`aud` 的預期值;也用來擋使用者 token)
    回傳: 可作為 `Depends()` 的函式,通過時回傳 `S2SCaller`
    副作用: 見模組檔頭

    判定順序是刻意的:**先認證(401),後授權(403)** ——
    一張壞 token 不該拿到「你缺 scope」這種**只有有效憑證才配知道**的答案。
    """

    def _guard(request: Request) -> S2SCaller:
        from app.repo import get_source_app   # 延後匯入,避免與 repo 互相 import

        token = _bearer_token(request)
        if token is None:
            raise _reject(request, 401, "missing_token", "沒有 Authorization: Bearer")

        try:
            claims = request.app.state.oidc.verify_access_token(token)
        except OidcError as exc:
            if exc.status_code != 401:
                raise                          # 503(IdP 不可用)原樣往上,不偽裝成 401
            raise _reject(request, 401, "invalid_token", exc.detail)

        azp = str(claims.get("azp") or "")
        # 🔴 `azp` 是我方自己 = 這是**使用者登入流程**簽給我方的 token
        #    (我方 client 的 service account 已確認停用,T03 回讀驗證)。
        #    它的 `aud` 可能因設定而含 `cats-inbox`、scope 也可能被加寬 ——
        #    但它代表的是一個**人**,不是一個服務。
        if azp == own_client_id:
            raise _reject(request, 401, "invalid_token", "azp 是我方自己:使用者 token 不是服務憑證")

        # 🔴 **整詞比對**:`scope` 是空白分隔字串。子字串比對的話
        #    `notification:pushall`、`notification:push:admin` 也會算數。
        granted = set(str(claims.get("scope") or "").split())
        if scope not in granted:
            raise _reject(request, 403, "insufficient_scope", f"缺 scope {scope}",
                          azp=azp, scope=scope)

        # 🔴 deny-by-default:查不到就是拒收,**不 fallback 成「未知來源」**,
        #    也**不看請求從哪裡來** —— 內網不是身分(§11.5 第 3 條)。
        #    每次請求都查、不快取:停用要**即時**生效(Q2:「可停用單一來源而不動程式」)。
        with session_scope() as db:
            row = get_source_app(db, azp)
            if row is None:
                raise _reject(request, 403, "source_not_registered", "azp 未登記", azp=azp)
            if not row.enabled:
                raise _reject(request, 403, "source_disabled", "來源已停用", azp=azp)
            return S2SCaller(azp=azp, sub=str(claims.get("sub") or ""), source_app=row.label)

    return _guard
