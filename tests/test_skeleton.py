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

為什麼是靜態斷言而不是 `docker compose up`:這些錯誤全部在**檔案內容**就決定了。
把它們釘在檔案層,錯誤會在 PR 就出現,不必等到 VM 上部署才發現——
後者的代價是「重建後 502」那一類已經有人踩過的坑。

🔴 **2026-08-28 更正(T05b):本段原本寫「而 CI runner 上跑不起真的 PostgreSQL+gateway」。**
   `gateway` 那半是對的(CI 上沒有整個平台可以反代),**`PostgreSQL` 那半是錯的**
   —— GitHub Actions 的 `services:` 就是用來跑它的。
   而錯的那半正是有影響的那半:CI 因此一直沒有 PG service,
   `test_migration.py` + `test_schema.py` 共 **13 支整批 skip**,綠燈照樣是綠燈。
   ⚠ **一個寫下來的錯誤理由比沒有理由更難發現** —— 它讀起來像已經有人判斷過了。
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


def test_env_example_db_url_driver_matches_installed_dbapi():
    """🔴 `INBOX_DB_URL` 的 driver 必須與 `requirements.txt` 實際裝的 DBAPI 相符。

    根本原因(2026-08-29 於 VM B-1 發現,**與上一條同一行、同一類**):
    `.env.example` 寫 `postgresql://`,而 SQLAlchemy 2.0 對這個 scheme 預設用
    **psycopg2**;`requirements.txt` 裝的是 **psycopg 3**(`psycopg[binary]`)。
    `create_engine()` 在**建立時**就載入 dialect → `ModuleNotFoundError: psycopg2`
    → `create_app()` 直接拋例外 → **容器在重啟迴圈裡**(restart policy 是 unless-stopped)。

    🔴 **為什麼測試沒抓到(第二次同樣的理由)**:測試走 `INBOX_TEST_DB_URL`,
       而 `tests/pg_local.sh` 給的是 **`postgresql+psycopg://`** —— 是對的那個。
       `.env.example` 那一行**只在真的部署時才被讀到**。
    ⚠ `app/main.py` 的 fallback 預設值也是 `postgresql+psycopg://` —— 也就是說
      **程式碼知道正確答案,而部署樣板不知道**。

    ⚠ 上一條(密碼欄位)只檢查了同一行的**一半**,所以同一行以第二種方式錯了一次。
      本條把 driver 也釘住,並與 `requirements.txt` **綁在一起**驗 ——
      哪天換回 psycopg2,兩邊會一起紅而不是靜靜地不一致。
    """
    reqs = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    has_psycopg3 = bool(re.search(r"^psycopg\[", reqs, re.M) or re.search(r"^psycopg==", reqs, re.M))
    has_psycopg2 = bool(re.search(r"^psycopg2", reqs, re.M))
    assert has_psycopg3 != has_psycopg2, (
        f"🔴 requirements 同時(或都不)裝 psycopg3/psycopg2:3={has_psycopg3} 2={has_psycopg2}"
    )
    want = "postgresql+psycopg://" if has_psycopg3 else "postgresql+psycopg2://"

    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    line = next((ln for ln in text.splitlines() if ln.startswith("INBOX_DB_URL=")), None)
    assert line, "🔴 `.env.example` 沒有 INBOX_DB_URL"
    value = line.split("=", 1)[1]
    assert value.startswith(want), (
        f"🔴 `INBOX_DB_URL` 的 driver 與實際裝的 DBAPI 不符:應以 `{want}` 開頭。"
        "⚠ 症狀不是連線失敗,是**容器在重啟迴圈裡** —— `create_engine()` 建立時就載入 "
        "dialect,找不到模組會讓 `create_app()` 拋例外。"
        f"現值:{value}"
    )


# =============================================================================
# T05b — CI 必須有真的 PostgreSQL 15,否則 13 支 schema/migration 測試整批 skip
# =============================================================================

CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

# 這 13 支測試需要的兩個變數。少一個不是「少測一點」:
# `tests/conftest.py::pg_models_engine` 用 `os.environ[...]`,少設會是 KeyError。
DB_ENV_VARS = ("INBOX_TEST_DB_URL", "INBOX_TEST_MODELS_DB_URL")


def _ci_yaml() -> dict:
    """讀 CI workflow 並以 YAML 解析。

    用途:給下面三條守門共用。
    回傳:解析後的 dict。
    副作用:無(只讀檔)。

    🔴 用 YAML 解析而不是 grep:「有沒有 `postgres` 這個字」與
       「有沒有一個 image 是 postgres:15 的 service」是兩件事,
       而前者會被註釋裡的 `postgres` 滿足。本專案已記過四次
       「斷言的粒度比它宣稱保護的性質粗」。
    """
    assert CI_WORKFLOW.exists(), f"🔴 找不到 {CI_WORKFLOW}"
    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))


def _tests_job(cfg: dict) -> dict:
    jobs = cfg.get("jobs") or {}
    assert "tests" in jobs, f"🔴 CI 沒有 `tests` job(有:{sorted(jobs)})"
    return jobs["tests"]


def test_ci_runs_tests_against_a_real_postgres_15():
    """🔴 CI 必須宣告一個 **PostgreSQL 15** 的 service。

    根本原因(2026-08-28 回寫進度表時發現):CI 從來沒有 `services:` 區塊,
    於是 `tests/test_migration.py`(3 支)與 `tests/test_schema.py`(10 支)
    **每一次 push 都整批 skip** —— 而它們正是 schema 與 migration 的測試,
    `test_migration_up_down_up` 就是共通紅線要求的 up→down→up 雙向演練本身。
    **第三條2 要求「測試納入 CI 防止回歸」,而這 13 支從 T05 至今沒有防護過任何一次 push。**

    ⚠ 症狀為什麼看不出來:`run_all.sh` 最後印「測試群組: N/N 通過」,
      CI 綠燈;skip 不是失敗。**「沒跑」與「跑過且綠」在綠燈上長得一樣。**

    🔴 **為什麼必須釘住是 15 而不是「有 postgres 就好」**:平台紅線是 PG 15,
       而 `tests/pg_local.sh` 自己就寫著「本機是 PG16,**PG16 演練不等於 PG15 演練**」。
       只驗「有 postgres 字樣」的話,`postgres:16` 會通過,
       而那正是這條守門要擋的那個誤會。
    """
    job = _tests_job(_ci_yaml())
    services = job.get("services") or {}
    images = {name: (spec or {}).get("image", "") for name, spec in services.items()}
    assert images, (
        "🔴 CI 的 `tests` job 沒有任何 `services:` —— "
        "13 支 schema/migration 測試會整批 skip,而 CI 仍然綠燈。"
    )
    pg = [img for img in images.values() if "postgres" in img]
    assert pg, f"🔴 CI 的 services 裡沒有 postgres:{images}"
    assert any(re.search(r"postgres:15(\.|-|$)", img) for img in pg), (
        f"🔴 CI 的 PostgreSQL 不是 **15**(平台紅線的版本):{pg}。"
        "⚠ PG16 演練不等於 PG15 演練 —— `tests/pg_local.sh` 的檔頭就是為這句話寫的。"
    )


def test_ci_passes_both_db_urls_to_the_test_step():
    """🔴 跑測試那一步必須拿到**兩個** `INBOX_TEST_*_DB_URL`。

    只設 `INBOX_TEST_DB_URL` 是最容易犯的錯 —— 兩個 skipif 都只看它,
    所以測試會**開始跑**,然後在 `tests/conftest.py::pg_models_engine` 的
    `os.environ["INBOX_TEST_MODELS_DB_URL"]` 上 **KeyError**。
    ⚠ 那個症狀看起來像「測試壞了」,而實際上是「CI 少設一個變數」。
    """
    job = _tests_job(_ci_yaml())
    steps = job.get("steps") or []
    runners = [s for s in steps if "run_all.sh" in str((s or {}).get("run", ""))]
    assert runners, "🔴 CI 沒有任何步驟跑 `tests/run_all.sh`"
    for step in runners:
        env = step.get("env") or {}
        missing = [v for v in DB_ENV_VARS if v not in env]
        assert not missing, (
            f"🔴 跑測試那一步少了 {missing};"
            f"有的是:{sorted(env)}。少設的後果不是 skip,是 KeyError。"
        )


def test_ci_db_urls_use_the_installed_dbapi_driver():
    """🔴 CI 的連線字串 driver 必須與 `requirements.txt` 實際裝的 DBAPI 相符。

    與 `.env.example` 那兩條同一個理由,但這裡是**第三個**會被讀到的地方:
    寫 `postgresql://` 而裝的是 psycopg 3 → `ModuleNotFoundError: psycopg2`。
    ⚠ 在 VM 上那個症狀是**容器重啟迴圈**(2026-08-29 實測);在 CI 上會是
      13 支測試整批 error —— 兩邊都不是「連線失敗」那種好認的訊息。
    """
    reqs = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    has_psycopg3 = bool(re.search(r"^psycopg\[", reqs, re.M) or re.search(r"^psycopg==", reqs, re.M))
    want = "postgresql+psycopg://" if has_psycopg3 else "postgresql+psycopg2://"

    job = _tests_job(_ci_yaml())
    checked = 0
    for step in job.get("steps") or []:
        env = (step or {}).get("env") or {}
        for var in DB_ENV_VARS:
            if var in env:
                checked += 1
                assert str(env[var]).startswith(want), (
                    f"🔴 CI 的 {var} 應以 `{want}` 開頭(實際裝的 DBAPI 決定),"
                    f"現值以 `{str(env[var]).split('://')[0]}://` 開頭"
                )
    # 🔴 一條「什麼都沒檢查到也會綠」的斷言不是保護,是裝飾。
    #    上一條守門保證這兩個變數存在,而**萬一有人把上一條刪了**,
    #    這一條會變成永遠通過而看起來仍在守門(本專案記過四次的形狀)。
    assert checked == len(DB_ENV_VARS), (
        f"🔴 這條守門只檢查到 {checked} 個連線字串(應為 {len(DB_ENV_VARS)})——"
        "它自己什麼都沒驗到,不是「通過」"
    )
