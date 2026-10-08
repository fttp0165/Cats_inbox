# -*- coding: utf-8 -*-
"""模板共用層(T08c):同一個 Jinja 環境、`taipei` 過濾器、導覽列的能力旗標。

為什麼要有這個模組:
  四個 router 原本各自 `Jinja2Templates(directory=…)` 一次 —— 四個環境、四份設定。
  要加一個過濾器就得改四處,漏一處的症狀是**那一頁 500**(未知過濾器),其他頁正常。
  現在只有一個入口 `make_templates()`,過濾器與設定只登記一次。

🔴 `taipei` 只管**畫面**。JSON API 仍走 `app/validation.py::iso_utc`(帶 `+00:00` 的 ISO 8601):
   輸入側拒收不帶時區的值,輸出側就不能吐不帶時區的值 —— 那是 API 的契約。
   畫面給人看,人在台北;API 給程式看,程式要時區。兩者分開,誰都不遷就誰。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.authz import (
    CAP_MANAGE_ROLES,
    CAP_PUBLISH_ANNOUNCEMENT,
    CAP_READ_OWN,
    has_capability,
)

# 🔴 固定 +08:00,不用 zoneinfo("Asia/Taipei"):
#    ① 台灣自 1979 年起沒有日光節約時間,固定位移與 tz 資料庫結果相同;
#    ② 執行期映像是 `python:3.13-slim`,系統 tzdata 不保證存在 —— 缺的話 zoneinfo 會丟
#       ZoneInfoNotFoundError,而症狀是**整個收件匣 500**,只為了顯示一個時間。
TAIPEI = timezone(timedelta(hours=8), name="Asia/Taipei")


def taipei(value: datetime | str | None) -> str:
    """把 UTC 時間轉成台北時間 `YYYY-MM-DD HH:MM`(Jinja 過濾器)。

    參數: value — aware/naive datetime、`iso_utc` 吐出的 ISO 字串、或 None/空字串
    回傳: 例 `2026-10-08 17:08`;None 或空字串回 `—`;看不懂的字串**原樣回傳**
    副作用: 無

    ⚠ naive 一律視為 UTC —— 與 `iso_utc` 同一個慣例(寫入端一律 UTC,naive 只在 SQLite 上出現)。
    ⚠ 看不懂的字串不丟例外:顯示用的過濾器丟 500 會把整頁炸掉,
      而原樣印出來至少讓人看得到那個值是什麼。
    """
    if value is None or value == "":
        return "—"
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return value
    elif isinstance(value, datetime):
        dt = value
    else:
        return str(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TAIPEI).strftime("%Y-%m-%d %H:%M")


def make_templates() -> Jinja2Templates:
    """建立**唯一一種**模板環境(目錄 `app/templates/`,登記 `taipei` 過濾器)。

    回傳: Jinja2Templates
    副作用: 無
    ⚠ 四個 router 各呼叫一次拿到各自的實例沒關係 —— 重點是設定只寫在這裡一處。
    """
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    templates.env.filters["taipei"] = taipei
    return templates


def nav_context(roles, *, active: str) -> dict:
    """由這個人的角色算出共用導覽列要顯示哪些入口。

    參數: roles — 該人的啟用角色;active — 現在這一頁(`inbox` / `publish` / `admin` / `pending`)
    回傳: dict(`can_read` / `can_publish` / `can_admin` / `nav_active`),直接 `**` 進 context
    副作用: 無

    🔴 只在**真的有能力**時才顯示入口(T09b 的原則):顯示一個點下去會 403 的連結
       **比不顯示更糟** —— 它讓人以為自己做錯了什麼,而其實他從來沒有那個權限。
       守門:`tests/test_ui_design.py::test_nav_hides_admin_and_publish_from_reader`。
    """
    return {
        "can_read": has_capability(roles, CAP_READ_OWN),
        "can_publish": has_capability(roles, CAP_PUBLISH_ANNOUNCEMENT),
        "can_admin": has_capability(roles, CAP_MANAGE_ROLES),
        "nav_active": active,
    }
