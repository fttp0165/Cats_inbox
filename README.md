# cats-inbox — Sporton 平台站內信/通知中心

**建立日期:** 2026-08-15 08:58
**最後更新:** 2026-09-23 15:50
**版本:** v1.2

> 系統通知 + 公告(階段一)、人對人站內信(階段二)、不做即時聊天。
> 獨立服務,掛統一入口 `catsapp.sporton.com.tw/inbox/`;收件人一律 IdP(Keycloak)的 `sub`。

## 現況

🔴 **現況只在一個地方維護:`docs/進度表.md`**(一頁掃完:到哪裡了、卡在誰身上、測試與發版)。
本檔 v1.1 曾自己寫一段現況(「T01 完成、下一個任務 T03」),而它在 T03 之後就是錯的、一路錯到 2026-09-23 —— 複製的現況會漂移,所以這裡不再複製。

一句話(2026-09-23):M0–M3 完成;`v0.1.1` 在 CATS VM 上跑著但**還不能從入口到達**(T11b 的 C/D 段);
推送 API 本體已完成(T14a)但**未部署**,接上第一個來源等 DI-4 與第一張憑證(T14b)。

## 文件鏈(md 為權威,HTML 為發布版)

| 讀什麼 | 位置 |
|---|---|
| 開發憲法(最高準則) | `CLAUDE.md` |
| **現在到哪裡了** | `docs/進度表.md` |
| 開發計畫書(when/who/how) | `docs/開發計畫書.md` |
| 任務表(逐任務追蹤) | `docs/任務表.md` |
| TDD 測試計畫表(每任務先寫哪支紅測試) | `docs/TDD測試計畫表.md` |
| 架構說明(商業邏輯 · 技術架構 · 介面概念) | `docs/架構說明.md` |
| 發版 SOP | `docs/發版SOP.md` |
| T11b 上線指令稿(可貼版) | `docs/T11b上線指令稿.md` |
| **給來源系統:推送 API 接入指南** | `docs/來源接入指南.md` |
| SSO 接入申請 · 契約對齊盤點 | `docs/SSO接入申請.md` · `docs/契約對齊盤點_v3.3.md` |
| 開發日誌(逐任務證據) | `docs/dev-logs/` |
| 功能上游(what/why) | cats-portal `DOCS/站內信通知中心設計規劃.md`(已對齊版本見 `CLAUDE.md` A.2) |

## 常用指令

跑測試(CI 同入口)。🔴 **第一行決定那 17 支 migration/schema 測試是「跑過」還是「skipped」** —— 沒有真 PostgreSQL 時它們會跳過,而跳過與通過在輸出上長得很像:

> 🖥️ **在哪執行:** WSL(Ubuntu)· 工作目錄 ~/Cats_inbox · 分支 `claude/cats-inbox-development-plan-ghwd9s`

```bash
pip install -r requirements-dev.txt
eval "$(bash tests/pg_local.sh start)"
bash tests/run_all.sh
```

本機起服務(未設 OIDC 環境變數時只有健康檢查;設齊之後登入、收件匣、公告、推送 API 才會註冊):

> 🖥️ **在哪執行:** WSL(Ubuntu)· 工作目錄 ~/Cats_inbox

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8080
curl -s localhost:8080/inbox/health
```

重產正式文件的 HTML 版(第四條;`--check` 只驗同步不寫檔):

> 🖥️ **在哪執行:** WSL(Ubuntu)· 工作目錄 ~/Cats_inbox

```bash
python3 tools/render_docs.py
```

## 技術形態(定案於開發計畫書 §3)

Python 3.13 + FastAPI + Jinja2(伺服器端算繪)+ PostgreSQL 15;容器 `cats-inbox-api`(上 `cats-edge`)+ `cats-inbox-pg`(僅內網);OIDC=Keycloak(confidential + PKCE);各系統以 S2S(client_credentials + scope `notification:push`)推送;未讀鈴鐺 30 秒輪詢,無 Redis/佇列/WebSocket。

---

## 版本歷史

| 版本 | 日期 | 修改人 | 摘要 |
|---|---|---|---|
| v1.2 | 2026-09-23 | Benny | 🔴 **「現況」一節從 T03 起就是錯的**(還寫著「T01 完成、下一個任務 T03 申請 client」「尚未接 SSO」「映像尚未建置推送」),而本檔是 repo 根目錄第一個被讀的東西。處置:**不再在本檔複製現況**,改指向 `docs/進度表.md`(D04 起唯一維護現況的地方,且有守門比對任務表),只留一句話摘要。文件鏈補上 D01 之後新增的五份文件(進度表、架構說明、發版 SOP、T11b 指令稿、來源接入指南);常用指令補上第九條11 的「在哪執行」標示與 `pg_local.sh`(不起它,17 支測試會 skipped)。T14a 同批發現 |
| v1.1 | 2026-08-15 | Claude(Benny) | T01 完成回寫:現況改為骨架可跑(48 項測試全綠)並誠實標註容器實際啟動未驗、映像未推送;文件鏈加入 TDD 測試計畫表;指令補測試依賴安裝與本機起服務 |
| v1.0 | 2026-08-15 | Claude(Benny) | 初版:專案定位、文件鏈、常用指令、技術形態;M0 完成標記 |
