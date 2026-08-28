# -*- coding: utf-8 -*-
"""T01 骨架層紅測試——compose 結構、Dockerfile、.env.example。

對應紅線(CLAUDE.md A.3 + 對齊指南 v1.3 + portal 容器紅線):
    - 只有對外容器 `cats-inbox-api` 上 `cats-edge`;`cats-inbox-pg` 不上、不曝 port
    - `cats-edge` 必須 external: true + name: cats-edge(對齊指南 §2.2:零歧義)
    - PostgreSQL 15(平台統一版本)
    - 禁 `build: .`(一邊運行一邊編譯)、禁 `latest` tag
    - 健康檢查與 restart policy 無特例
    - 容器 non-root、EXPOSE 8080
    - 所有 .env 變數必須在 .env.example 列明(缺漏=CI 失敗)

為什麼是靜態斷言而不是 `docker compose up`:這些錯誤全部在**檔案內容**就決定了,
而 CI runner 上跑不起真的 PostgreSQL+gateway。把它們釘在檔案層,錯誤會在 PR 就出現,
不必等到 VM 上部署才發現——後者的代價是「重建後 502」那一類已經有人踩過的坑。
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "docker-compose.yml"
DOCKERFILE = ROOT / "Dockerfile"
ENV_EXAMPLE = ROOT / ".env.example"
CONFIG_PY = ROOT / "app" / "config.py"

API_SERVICE = "cats-inbox-api"
PG_SERVICE = "cats-inbox-pg"


@pytest.fixture(scope="module")
def compose() -> dict:
    """讀入並解析 docker-compose.yml。缺檔即失敗(紅測試的起點)。"""
    assert COMPOSE.exists(), f"{COMPOSE} 不存在"
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


# ── compose:服務存在性與網路隔離 ───────────────────────────────────────────

def test_two_services_defined(compose):
    """只該有對外 API 與 DB 兩個服務(極簡棧:無 Redis、無佇列)。"""
    services = compose.get("services", {})
    assert API_SERVICE in services
    assert PG_SERVICE in services


def test_api_on_cats_edge(compose):
    """對外容器必須在 cats-edge 上,否則 gateway 反代不到(對齊指南 §2)。"""
    nets = compose["services"][API_SERVICE].get("networks") or {}
    assert "cats-edge" in nets


def test_pg_not_on_cats_edge(compose):
    """🔴 隔離線:DB 不得加入 cats-edge——它在共用網上等於對全平台可達。"""
    nets = compose["services"][PG_SERVICE].get("networks") or {}
    assert "cats-edge" not in nets, "cats-inbox-pg 不得加入 cats-edge(A.3 紅線)"


def test_cats_edge_declared_external_with_name(compose):
    """cats-edge 必須宣告為既存外部網並指名,不得由本 compose 另建一張同名網。"""
    edge = compose.get("networks", {}).get("cats-edge") or {}
    assert edge.get("external") is True, "cats-edge 必須 external: true"
    assert edge.get("name") == "cats-edge", "必須配 name: cats-edge(對齊指南 §2.2)"


# ── compose:port 暴露面 ─────────────────────────────────────────────────

def test_no_service_publishes_ports(compose):
    """全 VM 只有 gateway 對外;api 只 expose、pg 連 expose 都不需要。"""
    for name, svc in compose["services"].items():
        assert "ports" not in svc, f"{name} 不得對主機發布 port(容器紅線)"


def test_api_exposes_8080(compose):
    """gateway 以 cats-inbox-api:8080 反代,對外埠號是契約的一部分。"""
    assert 8080 in [int(p) for p in compose["services"][API_SERVICE].get("expose", [])]


# ── compose:映像與版本紅線 ───────────────────────────────────────────────

def test_no_build_directive(compose):
    """禁 `build:`——正式機一律 prebuilt image,不在機上編譯。"""
    for name, svc in compose["services"].items():
        assert "build" not in svc, f"{name} 不得使用 build:(一律 prebuilt image)"


def test_no_latest_tag(compose):
    """production 不用 latest:tag 浮動等於不知道跑的是哪一版。"""
    for name, svc in compose["services"].items():
        image = svc.get("image", "")
        assert image, f"{name} 缺 image"
        assert not image.endswith(":latest"), f"{name} 不得用 latest tag"
        assert ":" in image, f"{name} 的 image 必須釘版本"


def test_postgres_is_15(compose):
    """平台統一 PostgreSQL 15(資料庫版本紅線)。"""
    assert re.search(r"postgres:15", compose["services"][PG_SERVICE]["image"])


# ── compose:可用性設定 ───────────────────────────────────────────────────

@pytest.mark.parametrize("service", [API_SERVICE, PG_SERVICE])
def test_healthcheck_and_restart_policy(compose, service):
    """健康檢查與 restart policy 無特例(重開機自動恢復)。"""
    svc = compose["services"][service]
    assert "healthcheck" in svc, f"{service} 缺 healthcheck"
    assert svc.get("restart"), f"{service} 缺 restart policy"


def test_pg_has_named_volume(compose):
    """DB 必須有具名 volume,否則重建即資料全失。"""
    assert compose["services"][PG_SERVICE].get("volumes")
    assert compose.get("volumes")


def test_api_healthcheck_hits_health_endpoint(compose):
    """健康檢查要打 /inbox/health(不查 DB 的那支),不得改打會碰 DB 的路徑。"""
    hc = str(compose["services"][API_SERVICE]["healthcheck"])
    assert "/inbox/health" in hc


# ── Dockerfile ─────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def dockerfile() -> str:
    assert DOCKERFILE.exists(), f"{DOCKERFILE} 不存在"
    return DOCKERFILE.read_text(encoding="utf-8")


def test_dockerfile_runs_as_non_root(dockerfile):
    """容器 non-root:被打進來時的破壞半徑差在這一行。"""
    users = re.findall(r"^\s*USER\s+(\S+)", dockerfile, re.MULTILINE)
    assert users, "Dockerfile 缺 USER 指令(不得以 root 執行)"
    assert users[-1] not in ("root", "0"), f"最後的 USER 是 {users[-1]},必須非 root"


def test_dockerfile_exposes_8080(dockerfile):
    assert re.search(r"^\s*EXPOSE\s+8080", dockerfile, re.MULTILINE)


def test_dockerfile_base_image_pinned(dockerfile):
    """FROM 不得用 latest 或不帶 tag——重建出不同的東西是查不出來的那種 bug。"""
    froms = re.findall(r"^\s*FROM\s+(\S+)", dockerfile, re.MULTILINE)
    assert froms
    for image in froms:
        assert ":" in image, f"FROM {image} 未釘版本"
        assert not image.endswith(":latest"), f"FROM {image} 不得用 latest"


# ── .env.example ───────────────────────────────────────────────────────

def test_env_example_lists_every_variable_read_by_config():
    """程式讀的每個環境變數都必須在 .env.example 列名(缺漏=CI 失敗)。

    做法:自 app/config.py 掃出 `_env("NAME"` / `os.getenv("NAME"` 的變數名,
    逐一比對 .env.example。這條規則存在的理由是:少一個變數不會有錯誤訊息,
    只會在別人照著 .env.example 部署時,以一個難懂的方式壞掉。
    """
    assert CONFIG_PY.exists(), f"{CONFIG_PY} 不存在"
    assert ENV_EXAMPLE.exists(), f"{ENV_EXAMPLE} 不存在"
    src = CONFIG_PY.read_text(encoding="utf-8")
    names = set(re.findall(r'(?:os\.getenv|_env)\(\s*"([A-Z0-9_]+)"', src))
    assert names, "config.py 未讀取任何環境變數?請檢查掃描規則"
    listed = set(re.findall(r"^([A-Z0-9_]+)=", ENV_EXAMPLE.read_text(encoding="utf-8"),
                            re.MULTILINE))
    missing = names - listed
    assert not missing, f".env.example 缺少變數: {sorted(missing)}"


def test_env_example_oidc_values_match_portal_delivery_shape():
    """`.env.example` 的 OIDC 位址形狀必須與 portal 交付檔一致。

    🔴 這條是實際踩到才補的(T04):`.env.example` 原本把
    `INBOX_OIDC_INTERNAL_BASE` 寫成 `http://keycloak:8080/auth/realms/sporton`,
    而 portal 的 `idp/bootstrap/client-cats-inbox.sh` 交付的是
    `http://keycloak:8080/auth`(**不含 realm**),realm 由程式自 ISSUER 推導後接上。

    照舊值部署會組出 `/realms/sporton/realms/sporton` → discovery 404,
    而這件事**只會在第一個真人登入時出現**,本機離線測試全綠。
    """
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    internal = re.search(r"^INBOX_OIDC_INTERNAL_BASE=(.*)$", text, re.MULTILINE)
    assert internal, ".env.example 缺 INBOX_OIDC_INTERNAL_BASE"
    assert "/realms/" not in internal.group(1), (
        f"INBOX_OIDC_INTERNAL_BASE 不得含 /realms/(程式會自己接):{internal.group(1)}"
    )
    issuer = re.search(r"^INBOX_OIDC_ISSUER=(.*)$", text, re.MULTILINE)
    assert issuer and "/realms/" in issuer.group(1), (
        "INBOX_OIDC_ISSUER 必須含 /realms/<realm>——realm 由它推導"
    )
    assert issuer.group(1).startswith("https://"), "ISSUER 必須是對外 https 網址(契約 §2.4)"


def test_env_example_carries_no_real_secrets():
    """.env.example 只列變數名,不得帶可用的值(secret 絕不進 git)。"""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if any(t in key for t in ("SECRET", "PASSWORD", "TOKEN")):
            assert value.strip() in ("", '""'), f"{key} 在 .env.example 帶了值"


def test_env_file_is_gitignored():
    """.env 必須在 .gitignore 內——這條是共通紅線,值得有一個測試盯著。"""
    assert re.search(r"^\.env$", (ROOT / ".gitignore").read_text(encoding="utf-8"),
                     re.MULTILINE)

def test_env_example_db_url_carries_a_password_slot():
    """🔴 `INBOX_DB_URL` 必須帶密碼欄位(`user:...@`)。

    根本原因(2026-08-28 於 VM 部署 A 段時發現):
    `app/config.py` 把 `INBOX_DB_URL` **逐字**當連線字串用,而 `.env.example` 原本寫
    `postgresql://cats_inbox@cats-inbox-pg:5432/cats_inbox` —— **沒有密碼**。
    同時 compose 給 pg 設了 `POSTGRES_PASSWORD`,而 postgres 官方映像在有密碼時
    以 `scram-sha-256` 要求驗證 → 應用連不上,錯誤是
    `password authentication failed for user "cats_inbox"`。

    🔴 **為什麼測試沒抓到**:測試走 `INBOX_TEST_PG_URL`(本機 PG,自己帶認證),
       **從不用 `.env.example` 的那一行** —— 那一行只在真的部署時才被讀到。
    ⚠ 症狀還會指錯方向:健康檢查刻意不查 DB,所以容器 healthy、`/inbox/health` 200,
      而 `alembic upgrade head` 才失敗 —— 看起來像 migration 的問題,其實是連線字串。
    """
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    line = next(
        (ln for ln in text.splitlines() if ln.startswith("INBOX_DB_URL=")), None
    )
    assert line, "🔴 `.env.example` 沒有 INBOX_DB_URL"
    value = line.split("=", 1)[1]
    host_part = value.split("//", 1)[1].split("/", 1)[0]   # user[:pw]@host:port
    assert "@" in host_part, f"🔴 連線字串沒有帳號區段:{value}"
    userinfo = host_part.rsplit("@", 1)[0]
    assert ":" in userinfo, (
        "🔴 `INBOX_DB_URL` 沒有密碼欄位(`user:密碼@host`)—— "
        "pg 有 POSTGRES_PASSWORD 時會要求驗證,而應用是逐字用這個字串;"
        f"症狀是 alembic 失敗於 password authentication failed。現值:{value}"
    )
