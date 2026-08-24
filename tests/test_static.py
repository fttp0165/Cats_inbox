# -*- coding: utf-8 -*-
"""T08 靜態資源守門。

🔴 **本檔繼承的是 portal 2026-08-03 踩過的坑**,原話寫在它的
`portal-admin/app/static/assets/vendor/PROVENANCE.md` 裡:

> 放在 `static/vendor/` 的話,檔案在 repo 裡、路徑也算得對,
> **但 app 根本不會把它送出去** —— 瀏覽器拿到 404,整張清單畫不出來。
> 2026-08-03 第一版就是這樣上線的。

**這個失敗模式的症狀是「整頁沒有樣式」,而伺服器完全沒有錯誤。**
沒有人會回報「這頁的字體怪怪的」,而 CSS 沒載到的頁面在小螢幕上
可能根本讀不了。所以要有一支測試**實際去打那個 URL**,
不是檢查「檔案在不在 repo 裡」——後者正是 portal 當時通過的那種檢查。
"""

from __future__ import annotations

import re

from tests.conftest import ROOT, _login

# 模板裡的靜態資源引用長這樣:href="/inbox/assets/vendor/bootstrap.min.css"
_ASSET_RE = re.compile(r'(?:href|src)="(/inbox/assets/[^"]+)"')


def _referenced_assets() -> set[str]:
    """掃 `app/templates/` 底下所有模板,收集靜態資源的 URL。

    回傳: set[str](對外路徑)
    副作用: 無
    ⚠ 用 `conftest.ROOT` 而非相對路徑:`run_all.sh` 是 `cd tests/` 之後才跑
    pytest,相對路徑會讓這支測試依賴「從哪裡啟動」(T05 踩過)。
    """
    urls: set[str] = set()
    for path in sorted((ROOT / "app/templates").glob("*.html")):
        urls |= set(_ASSET_RE.findall(path.read_text(encoding="utf-8")))
    return urls


def test_templates_reference_at_least_one_asset():
    """先確認掃到東西——掃到 0 個時下一支測試會**空跑而通過**。

    🔴 沿用 `run_all.sh` 對「探索到 0 支」的同一個原則:
    零個資源不是「都正確」,是**沒有測**。
    """
    urls = _referenced_assets()
    assert urls, (
        "模板裡掃不到任何 /inbox/assets/ 資源 —— 若真的沒有,"
        "請刪掉本檔;留著一支空跑的測試比沒有測試更糟"
    )


def test_every_template_asset_is_actually_served(app_client):
    """🔴 模板引用的每一個靜態資源都必須**真的送得出來**(回 200)。

    這支測試打的是**實際的 URL**,不是檔案系統路徑 ——
    因為 portal 2026-08-03 的坑正是「檔案在、路徑對、而 app 不送」。
    """
    http, transport = app_client
    _login(http, transport)

    missing = []
    for url in sorted(_referenced_assets()):
        r = http.get(url)
        if r.status_code != 200:
            missing.append(f"{url} → {r.status_code}")
    assert not missing, (
        "🔴 這些靜態資源送不出來(檔案可能不在 StaticFiles 掛載的目錄底下):\n  "
        + "\n  ".join(missing)
    )


def test_vendored_files_match_recorded_hashes():
    """vendored 檔案的雜湊必須與 `vendored.sha256` 相符。

    用途有兩個:①證明 repo 裡那份沒有被就地改過(改過的第三方檔案
    在升版時會被覆蓋,而改動就消失了);②與 portal 那份比對時有依據。
    ⚠ MIT 授權要求保留授權聲明,故 `LICENSE.txt` 也一併斷言存在。
    """
    import hashlib

    vendor = ROOT / "app/static/assets/vendor"
    record = vendor / "vendored.sha256"
    assert record.exists(), "缺 vendored.sha256(無法驗證 vendored 檔案是否被動過)"

    for line in record.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        expected, name = line.split(None, 1)
        target = vendor / name.strip()
        assert target.exists(), f"{name} 在 vendored.sha256 裡但檔案不存在"
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        assert actual == expected, (
            f"🔴 {name} 的內容與記錄的雜湊不符(被就地改過?)\n"
            f"  記錄:{expected}\n  實際:{actual}"
        )

    assert (vendor / "bootstrap.min.css.LICENSE.txt").exists(), (
        "缺 bootstrap.min.css.LICENSE.txt —— MIT 要求保留授權聲明"
    )
    assert (vendor / "PROVENANCE.md").exists(), "缺 PROVENANCE.md(來源與升版方式)"
