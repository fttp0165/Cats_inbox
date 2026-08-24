# vendored 前端相依:來源與可重現性

**建立日期:** 2026-08-24 23:40
**最後更新:** 2026-08-24 23:40
**版本:** v1.0
**任務:** T08(見 `docs/dev-logs/2026-08-24_T08_讀取API與收件匣UI.md`)

---

## 為什麼這個檔案在 repo 裡

契約 **§4.10**:**零外部 CDN、offline 可開**。所以相依必須進 repo。

本專案不引入 node 建置步驟(A.1「刻意極簡」),故直接放發布版 CSS,
由模板以 `<link rel="stylesheet">` 載入。

---

## 清單

| 檔案 | 套件 | 版本 | 授權 | 來源 |
|---|---|---|---|---|
| `bootstrap.min.css` | [bootstrap](https://www.npmjs.com/package/bootstrap) | **5.3.8** | MIT | **複製自 `cats-portal/portal-admin/app/static/assets/vendor/bootstrap.min.css`** |

授權全文在 `bootstrap.min.css.LICENSE.txt`(MIT 要求保留授權聲明)。

### 為什麼是「從 portal 複製」而不是從 npm 下載

1. **本開發環境無法對外取得**(HTTPS proxy 對 jsdelivr 回 403),
   而 T08 需要它才能做完 —— 停在這裡等外網不是選項。
2. **平台已經有一份**:`portal-admin` 自 2026-08-03(其 T2.3)起本地託管
   同一個版本。沿用它讓 inbox 與平台後台的視覺一致,
   而視覺一致在同一個 hostname 之下(D2″)是使用者看得到的差別。
3. **可驗證是同一個檔**:`vendored.sha256` 的雜湊與 portal 那份
   **逐字相同**(`8f8173cb…e4ff`)。這比「我從網路上抓了一份」更強 ——
   後者無法證明抓到的是哪一份。

⚠ 要升版時:portal 那邊升版之後再同步過來,並更新 `vendored.sha256`。
**不要各自從網路抓** —— 兩邊會漂到不同的 patch 版,而 CSS 的差異
在畫面上是「某個元件的間距不太一樣」,沒有人會去查。

---

## 🔴 檔案必須在 `StaticFiles` 掛載的目錄底下(portal 2026-08-03 踩過)

portal 的 `PROVENANCE.md` 記著:

> 放在 `static/vendor/` 的話,檔案在 repo 裡、路徑也算得對,
> **但 app 根本不會把它送出去** —— 瀏覽器拿到 404,整張清單畫不出來。
> 2026-08-03 第一版就是這樣上線的。

本專案 `app/main.py` 掛的是:

```python
app.mount(f"{settings.base_path}/assets", StaticFiles(directory=app/static/assets), name="assets")
```

所以本目錄的正確位置是 **`app/static/assets/vendor/`**,
對外路徑是 `/inbox/assets/vendor/bootstrap.min.css`。

**守門:** `tests/test_static.py::test_every_template_asset_is_actually_served`
——它掃模板裡每一個靜態資源引用,實際打一次確認回 200。
⚠ 這個失敗模式的症狀是「整頁沒有樣式」,而**伺服器完全沒有錯誤**。

---

## 版本歷史

| 版本 | 日期 | 修改人 | 摘要 |
|---|---|---|---|
| v1.0 | 2026-08-24 | Benny | 初版(T08):Bootstrap 5.3.8 自 `portal-admin` 複製,雜湊逐字相同並記錄;沿用 portal 的 `PROVENANCE.md` / `vendored.sha256` 慣例,並繼承其「檔案必須在 mount 目錄下」的教訓(加守門測試) |
