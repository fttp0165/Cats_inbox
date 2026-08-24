# -*- coding: utf-8 -*-
"""授權判定:角色 → 能力的**寫死**對應。

用途: 把「誰能做什麼」集中成一張常數表,讓範圍可以被逐條反向測試。
副作用: 無(純判定,不查 IdP、不寫任何東西)。

🔴 本模組是 portal 2026-08-18 核可 DEC-16 的**條件 C1 的落點**:
   「範圍寫死在自我範圍讀取」。因此:

   - 對應表是**模組層常數**,不是設定檔、不是資料庫——設定檔可以被改寬,
     而改寬不會有任何症狀(功能上一切正常)。
   - `sender` / `announcer` / `admin` **一律 deny-by-default**,
     不得援引該核可(核可只涵蓋 `reader`)。
   - 以**反向測試**釘住(條件 C3):`tests/test_authz.py` 裡
     `test_reader_cannot_send_message_403` 與 `..._publish_announcement_403`。

🔴 本模組**不得讀 `display_name`**(契約 §4.2a L1 第 4 條:快取不得用於
   認證/授權/身分判定)。以 `test_display_name_never_read_in_authz_path`
   的**源碼層** AST 檢查釘住——行為測試只能證明「這條路徑現在沒讀」,
   源碼檢查證明「沒有任何一條路徑讀得到」。
"""

from __future__ import annotations

from fastapi import Depends

from app.oidc import OidcError

# ── 角色 ────────────────────────────────────────────────────────────
ROLE_READER = "reader"        # 唯一會自動授與的角色(DEC-16)
ROLE_SENDER = "sender"        # 寄站內信;deny-by-default
ROLE_ANNOUNCER = "announcer"  # 發公告;deny-by-default
ROLE_ADMIN = "admin"          # 角色後台;deny-by-default

ALL_ROLES = (ROLE_READER, ROLE_SENDER, ROLE_ANNOUNCER, ROLE_ADMIN)

# ── 能力 ────────────────────────────────────────────────────────────
CAP_READ_OWN = "read_own"                      # 讀自己的訊息/標自己已讀/看有效公告
CAP_SEND_MESSAGE = "send_message"
CAP_PUBLISH_ANNOUNCEMENT = "publish_announcement"
CAP_MANAGE_ROLES = "manage_roles"

# 🔴 這張表就是「範圍」本身。加一項之前先問:**它是否仍限於自我範圍讀取?**
#    `reader` 那一列若多出任何一個能力,DEC-16 的核可基礎就不成立了。
ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    ROLE_READER: frozenset({CAP_READ_OWN}),
    ROLE_SENDER: frozenset({CAP_SEND_MESSAGE}),
    ROLE_ANNOUNCER: frozenset({CAP_PUBLISH_ANNOUNCEMENT}),
    # ⚠ admin **刻意不含** CAP_READ_OWN:管身分與讀信是兩件事。
    #   管理員也需要讀自己的信時,他身上另外有 reader(首登就有)。
    #   把兩者綁在一起會讓「單獨停用 reader」對管理員失效(C2)。
    ROLE_ADMIN: frozenset({CAP_MANAGE_ROLES}),
}


def capabilities_of(roles) -> frozenset[str]:
    """把角色清單攤成能力集合。

    參數: roles — 角色名的可迭代物(通常是 `AppUser.active_roles()`)
    回傳: frozenset[str]
    副作用: 無
    未知角色一律忽略(不是錯誤):資料庫裡可能留著已淘汰的角色名,
    而讓它靜靜地不給任何能力,比拋錯安全。
    """
    caps: set[str] = set()
    for role in roles:
        caps |= ROLE_CAPABILITIES.get(role, frozenset())
    return frozenset(caps)


def has_capability(roles, capability: str) -> bool:
    """這組角色有沒有這個能力。"""
    return capability in capabilities_of(roles)


class Forbidden(OidcError):
    """已認證但無此權限 → **403**。

    🔴 與 401 分開,不得混用(平台紅線,契約 §11.5 同一分界):
      - 401 = 憑證無效(沒登入、token 壞、過期)
      - 403 = 憑證有效但**無此權限**(→ 待開通頁)
    混用會讓查問題的人分不出「是我沒登入」還是「我沒被開通」,
    而這兩件事的下一步完全不同。
    """

    def __init__(self, capability: str) -> None:
        super().__init__(403, "forbidden", f"缺少能力:{capability}")


def require_capability(capability: str):
    """產生一個「要求某個能力」的 FastAPI 相依。

    參數: capability — 上面 `CAP_*` 之一
    回傳: 可作為 `Depends()` 的函式,通過時回傳 (sub, roles)
    副作用: 讀一次資料庫取角色
    錯誤: 未登入 → 401(由 T04 的 session 相依發出);已登入無權 → 403

    用法:
        @router.post("/messages", dependencies=[Depends(require_capability(CAP_SEND_MESSAGE))])
    """
    from app.deps import current_identity  # 延後匯入,避免與 routes_auth 互相 import

    def _guard(identity=Depends(current_identity)):
        sub, roles = identity
        if not has_capability(roles, capability):
            raise Forbidden(capability)
        return sub, roles

    return _guard
