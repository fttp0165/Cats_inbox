# -*- coding: utf-8 -*-
"""環境設定讀取。

用途:把所有外部設定集中在一處,讓「程式讀了哪些環境變數」可被機器掃描
      (`tests/test_skeleton.py` 據此比對 `.env.example`,缺漏即 CI 失敗)。
副作用:無。只讀 `os.environ`,不連線、不寫檔。

🔴 刻意的設計限制:secret 類變數**沒有預設值**。
   寫死的預設值會一路跟著跑進正式環境,而且不會有任何錯誤訊息;
   讓它是空的,啟動時就會明確失敗(fail-closed)。
"""

import os
from dataclasses import dataclass
from functools import lru_cache


def _env(name: str, default: str = "") -> str:
    """讀取環境變數。

    參數: name — 變數名(全大寫);default — 找不到時的值,secret 一律不給預設
    回傳: 字串(未設定時為 default)
    副作用: 無
    包成函式而非直接呼叫 os.getenv,是為了讓變數名可被靜態掃描到——
    `.env.example` 的完整性檢查靠的就是這個形狀。
    """
    return os.getenv(name, default)


@dataclass(frozen=True)
class Settings:
    """一次載入的設定快照(frozen:執行中不得被改寫)。"""

    # ── 服務本身 ──
    base_path: str          # 掛載子路徑;D2″ 之下本服務固定為 /inbox
    log_level: str          # log 走 stdout 單行 JSON(紅線:不記個資)
    db_url: str             # PostgreSQL 15 連線字串;健康檢查刻意不碰它

    # 對外站台位址(D2″ 單一 hostname)。
    # 🔴 用途:組 `redirect_uri`。它**必須逐字等於** client 的登記值,
    #    所以刻意是一個明確的設定值,而不是從請求的 Host header 推導——
    #    Host 可以被代理改寫,而契約 v2.14 記載的 PLM 事故就是實際送出值
    #    與登記值不同,錯誤停在 Keycloak 頁面、自家 log 一片空白。
    public_base_url: str

    # ── OIDC(契約 §2.4:伺服器端走內部位址,但 iss 維持對外)──
    oidc_issuer: str            # 驗 token 的 iss,**必須是對外網址**,不得改成內部位址
    oidc_internal_base: str     # 容器內實際連線用的 base(discovery/JWKS/token 端點)
    oidc_client_id: str         # 同時是驗 aud 的預期值(契約 §3.1:aud == client_id)
    oidc_client_secret: str     # confidential client;走 env,不進 git

    # ── session(契約 §10.2 聲明形態=cookie/伺服器 session)──
    session_secret: str         # 簽 session cookie 用;無預設值

    # ── 首登開通(契約 §4.3:bootstrap 管理員清單,每次登入比對)──
    bootstrap_admin_subs: str   # 以逗號分隔的 sub 清單;空=無 bootstrap 管理員


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """回傳設定快照(整個行程只讀一次)。

    回傳: Settings
    副作用: 無(僅讀環境變數並快取)
    測試需要重新讀取時,`importlib.reload(config)` 會連同快取一起重建。
    """
    return Settings(
        base_path=_env("INBOX_BASE_PATH", "/inbox"),
        log_level=_env("INBOX_LOG_LEVEL", "INFO"),
        db_url=_env("INBOX_DB_URL"),
        public_base_url=_env("INBOX_PUBLIC_BASE_URL", "https://catsapp.sporton.com.tw"),
        oidc_issuer=_env("INBOX_OIDC_ISSUER"),
        oidc_internal_base=_env("INBOX_OIDC_INTERNAL_BASE"),
        oidc_client_id=_env("INBOX_OIDC_CLIENT_ID"),
        oidc_client_secret=_env("INBOX_OIDC_CLIENT_SECRET"),
        session_secret=_env("INBOX_SESSION_SECRET"),
        bootstrap_admin_subs=_env("INBOX_BOOTSTRAP_ADMIN_SUBS"),
    )
