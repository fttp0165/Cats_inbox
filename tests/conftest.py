# -*- coding: utf-8 -*-
"""pytest 共用設定。

為什麼需要這支:測試以 `from app.main import app` 匯入應用,而 pytest 的
rootdir 不保證在 sys.path 上。這裡把 repo 根目錄插到最前面,讓測試
不論從 repo 根或 tests/ 下啟動都能匯入——CI 與本機的啟動位置本來就不同,
靠「記得 cd 對地方」是會壞的。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ─────────────────────────────────────────────────────────────────────
# OIDC 測試共用件(T04 建立,T06 起由 test_auth.py 與 test_logout.py 共用)
#
# 為什麼放 conftest 而不是留在 test_auth.py:
#   pytest 的 fixture 不跨測試檔共用,除非放在 conftest。原本讓
#   test_logout.py 去 `from tests.test_auth import ...` 也能跑,但那是
#   測試檔互相 import——誰先被收集會影響誰,而那種失敗是隨機的。
# ─────────────────────────────────────────────────────────────────────
import importlib
import urllib.parse

import pytest

from tests.fake_idp import CLIENT_ID, ISSUER, FakeIdP

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
def app_client(idp, clock, monkeypatch, db_session, sqlite_url):
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
    # T05 起建號要寫 DB;`db_session` fixture 已建好 schema 並共用同一個 engine
    monkeypatch.setenv("INBOX_DB_URL", sqlite_url)
    import app.config as config

    config.get_settings.cache_clear()

    import app.main as main

    importlib.reload(main)
    transport = FakeTransport(idp, clock)
    application = main.create_app(transport=transport, clock=clock)
    yield TestClient(application, base_url="https://testserver"), transport
    config.get_settings.cache_clear()


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
    assert r.status_code == 302, f"callback 應 302,實得 {r.status_code} {r.text[:200]}"


# ─────────────────────────────────────────────────────────────────────
# T05:資料庫 fixture
#
# 🔴 **兩種資料庫刻意分工:**
#   · 應用層行為測試(建號、角色、403)用 **SQLite 檔案**——可攜、CI 跑得動、
#     每個 test 一個乾淨的庫。
#   · **migration 測試用真的 PostgreSQL**(`tests/test_migration.py`)——
#     SQLite 對 DDL 太寬鬆,在它上面綠的 migration 到 PG 上可能直接失敗,
#     而那會發生在部署當下。
#
#   ⚠ 這個分工有一個**已知的盲點**:應用層用的 schema 是 `create_all()`
#     從 model 產生的,不是 migration 產生的。兩者若漂移,應用層測試不會發現。
#     `tests/test_migration.py::test_schema_has_no_identity_columns` 就是補這個
#     盲點——它驗的是**migration 本身**產出的 schema。
# ─────────────────────────────────────────────────────────────────────
@pytest.fixture()
def sqlite_url(tmp_path) -> str:
    """每個 test 一個獨立的 SQLite 檔(不用 :memory:,因為多連線看不到同一個庫)。"""
    return f"sqlite+pysqlite:///{tmp_path / 'inbox.db'}"


@pytest.fixture()
def db_session(sqlite_url):
    """建好 schema 的 DB session,且應用與測試**共用同一個 engine**。

    回傳: Session
    副作用: 建表;測試結束關閉

    🔴 共用 engine 是關鍵:應用透過 `app.db.session_scope()` 寫入,
    測試若另開一個 engine 就會看到另一個庫,斷言會全部失敗——
    而失敗訊息會看起來像「建號沒生效」,查錯方向。
    """
    from app.db import init_engine
    from app.models import Base

    engine = init_engine(sqlite_url)
    Base.metadata.create_all(engine)
    from sqlalchemy.orm import Session

    session = Session(bind=engine, expire_on_commit=False)
    yield session
    session.close()


def _build_app(idp, clock, monkeypatch, sqlite_url, **env):
    """共用的 app 建置流程(T05 的三個 app fixture 只差 env)。"""
    from fastapi.testclient import TestClient

    _set_oidc_env(monkeypatch)
    monkeypatch.setenv("INBOX_DB_URL", sqlite_url)
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    import app.config as config

    config.get_settings.cache_clear()

    import app.main as main

    importlib.reload(main)
    transport = FakeTransport(idp, clock)
    application = main.create_app(transport=transport, clock=clock)
    return TestClient(application, base_url="https://testserver"), transport


@pytest.fixture()
def app_client_no_autogrant(idp, clock, monkeypatch, db_session, sqlite_url):
    """自動授 reader **關閉**(核可條件 C4:portal 單方撤回後回到全 deny)。"""
    client, transport = _build_app(
        idp, clock, monkeypatch, sqlite_url, INBOX_AUTO_GRANT_READER="0"
    )
    yield client, transport
    import app.config as config

    config.get_settings.cache_clear()


@pytest.fixture()
def app_client_bootstrap(idp, clock, monkeypatch, db_session, sqlite_url):
    """把替身那個 sub 放進 bootstrap 管理員清單。"""
    client, transport = _build_app(
        idp, clock, monkeypatch, sqlite_url,
        INBOX_BOOTSTRAP_ADMIN_SUBS="11111111-2222-3333-4444-555555555555",
    )
    yield client, transport
    import app.config as config

    config.get_settings.cache_clear()


# ─────────────────────────────────────────────────────────────────────
# migration 測試專用:真的 PostgreSQL
# ─────────────────────────────────────────────────────────────────────
@pytest.fixture()
def pg_engine():
    """連到 `INBOX_TEST_DB_URL`,並在測試前後把 schema 清乾淨。

    副作用: DROP 掉本測試會用到的表與 alembic 版本表
    ⚠ 只跑在丟棄式的本機庫上(`tests/pg_local.sh` 起的),不指向任何正式庫。
    """
    import os

    from sqlalchemy import create_engine, text

    url = os.environ["INBOX_TEST_DB_URL"]
    engine = create_engine(url, future=True)
    with engine.begin() as conn:
        # 依外鍵相依的反序 drop(T07 的三張表在前)
        for tbl in ("announcement_read", "announcement", "message",
                    "user_role", "app_user", "alembic_version"):
            conn.execute(text(f"DROP TABLE IF EXISTS {tbl} CASCADE"))
    yield engine
    engine.dispose()


@pytest.fixture()
def alembic_cfg(pg_engine):
    """指向 repo 內 `alembic.ini` 的 Config(連線字串由 env.py 自 env 讀)。"""
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    return cfg


@pytest.fixture()
def models_engine():
    """另一個空的資料庫,用來跑 `Base.metadata.create_all()`。

    副作用: DROP 掉該庫內的所有本專案表
    🔴 為什麼要**第二個**資料庫:T07 的 `test_models_match_migration_schema`
       要比對「migration 建的」與「`create_all()` 建的」兩種 schema,
       它們不能建在同一個庫裡——後建的會撞到先建的。
    """
    import os

    from sqlalchemy import create_engine, text

    url = os.environ["INBOX_TEST_MODELS_DB_URL"]
    engine = create_engine(url, future=True)
    with engine.begin() as conn:
        for tbl in ("announcement_read", "announcement", "message",
                    "user_role", "app_user", "alembic_version"):
            conn.execute(text(f"DROP TABLE IF EXISTS {tbl} CASCADE"))
    yield engine
    engine.dispose()
