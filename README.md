# cats-inbox — Sporton 平台站內信/通知中心

**建立日期:** 2026-08-15 08:58
**最後更新:** 2026-08-15 09:58
**版本:** v1.1

> 系統通知 + 公告(階段一)、人對人站內信(階段二)、不做即時聊天。
> 獨立服務,掛統一入口 `catsapp.sporton.com.tw/inbox/`;收件人一律 IdP(Keycloak)的 `sub`。

## 現況

**T01 完成(2026-08-15):** 服務骨架可跑——`GET /inbox/health` 回 200(不查 DB)、compose 網路隔離與容器紅線以測試釘住、CI 綠。測試 48 項全綠(文件 25 + 骨架 19 + 應用 4)。
⚠ **容器實際啟動尚未驗**(開發環境無 docker daemon),留 T11 於 VM 部署時補;映像 tag `ghcr.io/fttp0165/cats-inbox-api:0.1.0` 尚未建置推送。
下一個任務:**T03 申請 Keycloak client**(T02 回寫 portal 設計規劃可並行)。

## 文件鏈(md 為權威,HTML 為發布版)

| 讀什麼 | 位置 |
|---|---|
| 開發憲法(最高準則) | `CLAUDE.md` |
| 開發計畫書(when/who/how) | `docs/開發計畫書.md` |
| 任務表(逐任務追蹤) | `docs/任務表.md` |
| TDD 測試計畫表(每任務先寫哪支紅測試) | `docs/TDD測試計畫表.md` |
| 開發日誌(逐任務證據) | `docs/dev-logs/` |
| 功能上游(what/why) | cats-portal `DOCS/站內信通知中心設計規劃.md` v1.3(選址變更見計畫書 §1.2) |

## 常用指令

跑測試(CI 同入口;首次需先裝測試依賴):

```
pip install -r requirements-dev.txt
bash tests/run_all.sh
```

本機起服務(尚未接 SSO,只有健康檢查):

```
uvicorn app.main:app --host 0.0.0.0 --port 8080
curl -s localhost:8080/inbox/health
```

重產正式文件的 HTML 版(第四條;`--check` 只驗同步不寫檔):

```
python3 tools/render_docs.py
```

## 技術形態(定案於開發計畫書 §3)

Python 3.13 + FastAPI + Jinja2(伺服器端算繪)+ PostgreSQL 15;容器 `cats-inbox-api`(上 `cats-edge`)+ `cats-inbox-pg`(僅內網);OIDC=Keycloak(confidential + PKCE);未讀鈴鐺 30 秒輪詢,無 Redis/佇列/WebSocket。

---

## 版本歷史

| 版本 | 日期 | 修改人 | 摘要 |
|---|---|---|---|
| v1.0 | 2026-08-15 | Claude(Benny) | 初版:專案定位、文件鏈、常用指令、技術形態;M0 完成標記 |
| v1.1 | 2026-08-15 | Claude(Benny) | T01 完成回寫:現況改為骨架可跑(48 項測試全綠)並誠實標註容器實際啟動未驗、映像未推送;文件鏈加入 TDD 測試計畫表;指令補測試依賴安裝與本機起服務 |
