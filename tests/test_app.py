# -*- coding: utf-8 -*-
"""T01 應用層紅測試——健康檢查、base path、版本常數。

對應驗收:開發計畫書 §3.2(容器紅線:`/health` 不查 DB)、
契約 §4.10(前端 base path 必須是自己的子路徑)、憲法第八條4(版本常數)。

這些測試釘住的是「上線當天才會發現」的那類錯誤:
    - 健康檢查查了 DB → DB 一慢,orchestrator 就把好好的容器判死
    - base path 沒掛前綴 → 本機測都對,經 gateway 一律 404
    - 版本常數與 tag 各講各的 → 換版驗證判錯
"""

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    """建立測試用 client,並**刻意把 DB 指向不存在的主機**。

    回傳: TestClient
    副作用: 以 monkeypatch 設定環境變數,測試結束自動還原。

    為什麼要指向壞掉的 DB:健康檢查「沒查 DB」與「剛好查得到」在
    正常環境下的觀測結果完全相同。把 DB 弄壞是唯一能分辨兩者的方法。
    """
    monkeypatch.setenv(
        "INBOX_DB_URL",
        "postgresql://nobody:nothing@db-does-not-exist.invalid:5432/nope",
    )
    from app.main import app  # 延後匯入,確保吃到上面設定的環境變數

    return TestClient(app)


def test_health_returns_200_without_database(client):
    """健康檢查在 DB 完全連不上時仍須回 200(容器紅線:/health 不查 DB)。"""
    r = client.get("/inbox/health")
    assert r.status_code == 200, f"預期 200,實得 {r.status_code}"
    assert r.json()["status"] == "ok"


def test_health_version_matches_constant(client):
    """健康檢查回報的版本必須等於 `app.__version__`(第八條4:單一來源)。"""
    import app as app_pkg

    r = client.get("/inbox/health")
    assert r.json()["version"] == app_pkg.__version__


def test_routes_are_mounted_under_inbox_prefix(client):
    """未帶 `/inbox` 前綴的路徑必須 404——base path 錯誤要在測試就爆,不是上線才爆。"""
    assert client.get("/health").status_code == 404


def test_no_secret_defaults_in_config():
    """設定不得內建任何可用的 secret 預設值(紅線:secret 不進 git)。

    語意:未設定 `INBOX_SESSION_SECRET` 時必須是 None/空,讓啟動時明確失敗,
    而不是靜默用一個寫死的字串——寫死的預設值會一路帶到正式環境。
    """
    for var in ("INBOX_SESSION_SECRET", "INBOX_OIDC_CLIENT_SECRET"):
        os.environ.pop(var, None)
    import importlib

    import app.config as config

    importlib.reload(config)
    settings = config.get_settings()
    assert not settings.session_secret
    assert not settings.oidc_client_secret
