# 2026-08-27 T11a gateway 路由設定入 portal(T11 的前半)

**建立日期:** 2026-08-27 11:35
**最後更新:** 2026-08-27 12:05
**版本:** v1.1
**對應任務:** **T11a**(T11 拆分後的前半;第一條4)

> 本篇計畫段於**動工前**寫妥(第二條2)。
> ⏱ 時間戳來源:開發機 `date` = `2026-08-27 03:32 UTC` → 本檔一律標 UTC+8。

---

## 計畫段(動工前寫妥,第二條2)

### 🔴 為什麼把 T11 拆成兩半(第一條4:排程變更先改任務表)

T11 原本是一列:「cats-portal 提 PR;VM 部署;低峰套用」。這三件事的**阻塞條件不同**:

| 半 | 內容 | 現在能不能做 |
|---|---|---|
| **T11a** | portal `gateway/nginx/conf.d/default.conf` 加 `/inbox/` 的 PR | ✅ **能** —— 純設定檔,不碰正式站 |
| **T11b** | VM 建 `/opt/cats-inbox`、併 client secret、`docker compose up -d`、低峰套用 gateway | ⛔ **不能** —— 需要 VM 與 Benny 的低峰窗口 |

合成一列的後果是**整列卡住**:能做的那一半跟著不能做的那一半一起等,
而等的過程中沒有任何產出。拆開之後 T11b 的低峰窗口只需要**貼指令**,
不需要當場想設定怎麼寫 —— 🔴 而「當場想設定」正是本專案最不該發生的事
(D8 紅線:動 gateway 就是動綁 80/443、全 VM 唯一的入口)。

### 設定怎麼寫,以及三個會靜默出錯的地方

**① `proxy_pass` 不可帶尾斜線 —— 帶了就是全站 404。**

本服務**自己擁有 `/inbox` 前綴**(`INBOX_BASE_PATH=/inbox`,路由是
`/inbox/health`、`/inbox/oidc/callback/`、`/inbox/assets/…`)。
nginx 的規則是 `proxy_pass` **只要帶了 URI**,匹配到的 location 前綴就會被替換掉:

```nginx
proxy_pass http://cats-inbox-api:8080/;   # ❌ 剝掉 /inbox/ → 上游收到 /health → 404
proxy_pass http://cats-inbox-api:8080;    # ✅ 純主機、無 URI → 原樣轉發 /inbox/health
```

⚠ 這與 `/upload/` **相反**(那支 app 不擁有自己的前綴,所以要剝)。
同一個檔案裡兩種寫法並存是對的,而 portal 自己在 2026-07-29 為了這件事
**改過一次寫反的註解**(default.conf:246)。

**② 靜態檔不需要另開 location。**
`/upload/static/` 之所以要單獨一條,是因為那支 app 的靜態檔在 `/static/`。
本服務掛在 `{base_path}/assets`,已經在 `/inbox/` 前綴之內 —— 多開一條反而會出錯。

**③ 🔴 本 location 一個 `add_header` 都不寫。**
nginx 的規則是「location 層只要有**任一** `add_header`,就**完全不繼承**上層」。
server 層有 `X-Content-Type-Options` 與 `X-Frame-Options DENY`;
本服務又自己送嚴格 CSP(含 `frame-ancestors 'none'`)與 `Referrer-Policy`。
不寫 add_header → 兩邊都在,且方向一致。
⚠ portal 在 2026-07-27 出過這個事故(default.conf:288「本區塊原本一個
add_header 都沒有」),而**它的兩個方向都會出事**:寫了會掉上層的,
不寫會缺自己的。本服務屬於後者不成立的情況(自己的標頭由應用送)。

### 🔴 一個我**刻意不做**的改動:不把 `/inbox/` 加進 `ops/gateway-apply.sh` 的驗證清單

那五條(`/`、`/plm/`、`/core/`、`/upload/`、`/sysadm/`)是**自動回滾的觸發條件** ——
任一條不通就把整份 gateway 設定回滾。把 `/inbox/` 加進去等於:

> **一個還沒上線的新服務,一旦掛掉就有權把 PLM / AES_KEY / core 的路由改動整批回退。**

那份清單守的是**平台既有正式路由的回歸**,不是每個 app 的健康檢查。
`/inbox/` 的驗證屬於 **T11b 套用後的冒煙**(`curl -skI …/inbox/health`),
不是回滾條件。
⚠ 連帶:portal `tests/test_gateway_apply.sh:127` 正好釘住那五條 ——
**不動它就是對的**,不是漏改。

### 上游容器不存在時的風險(這一條決定套用順序)

`proxy_pass` 寫死主機名時,nginx 是在**載入設定當下**解析的;容器不存在會是
`[emerg]` **讓整份設定失效** → gateway 起不來 → **PLM 與 AES_KEY 一起中斷**(D8 紅線)。
default.conf:228 已經為 `/upload/` 記過這件事。

→ 因此 **T11b 的順序不可顛倒**:先讓 `cats-inbox-api` 在 `cats-edge` 上跑起來,
**再**套用 gateway 設定。`ops/gateway-apply.sh` 會用一次性容器先 `nginx -t`,
容器沒起來時它會**拒絕套用**而不是套壞 —— 順序由工具擋著,不只靠人記得。
⚠ 反向也要記:哪天 `cats-inbox-api` 要下線,**必須先把這段改回註解再停容器**。

💡 根治方式是 `resolver 127.0.0.11` + 變數式 `proxy_pass`(對齊指南 §3.2.1,
portal 標為「待辦,尚未實作」)—— 那樣上游沒起來只會讓 `/inbox/` 回 502,
不會炸掉整個 gateway。**本次不做**:我無法在此環境跑 `nginx -t`,
而變數式 proxy_pass 的 URI 處理語意與字面式不同,**未實測的寫法不進正式設定檔**。
列為給 portal 的建議。

### 影響範圍

| 對象 | 影響 |
|---|---|
| cats-inbox 程式 / 資料庫 | 🟢 完全不動 |
| portal `gateway/nginx/conf.d/default.conf` | 🟡 **加兩個 location**;既有 location 一行不動 |
| 正式站(PLM / AES_KEY / core / upload / sysadm) | 🟢 **本次 PR 零影響** —— 設定進 repo 不等於套用;套用是 T11b |
| `ops/gateway-apply.sh` / `tests/test_gateway_apply.sh` | 🟢 **刻意不動**(理由見上) |
| portal `CLAUDE.md` 系統清單 | 🟡 cats-inbox 那列「路由待其 T11 提 PR」→ 已入設定、待低峰套用 |

### 驗收標準

1. portal `default.conf` 新增 `location = /inbox`(301 補尾斜線)與 `location /inbox/`;
   `proxy_pass` **無尾斜線**;四個 proxy header 齊備;**零 `add_header`**。
2. portal 測試全綠 —— 🔴 **含 `test_gateway_apply.sh` 仍釘那五條**(不因新增路由而變)。
3. portal `DOCS/dev-logs/` 新增日誌 + **同一次提交回寫開發計畫書**(portal 第六條4)。
4. portal `CLAUDE.md` 系統清單那列改狀態;升版 + 版本歷史。
5. cats-inbox `docs/任務表.md`:T11 拆為 **T11a ✅ / T11b ⬜**,並寫明 T11b 的阻塞條件。
6. cats-inbox 測試維持 234 項全綠(本任務不動程式,但第三條4 要求既有測試當安全網)。

### 回滾方式

`git revert`(portal 一次)。
⚠ **本次 PR 不套用到正式站**,所以回滾不需要低峰窗口 —— 這正是拆成兩半的好處:
前半的回滾成本是零。

---

## 🔴 計畫段被實測推翻的兩處(第二條5,不刪原文)

### 推翻 ①:設定**不能**直接生效,只能以**註解**入庫

計畫段寫「portal `default.conf` 新增 `location = /inbox` 與 `location /inbox/`」。
實際做完之後 `bash tests/run-all.sh` **兩支紅**:

```
❌ 🔴 生效中的 upstream 有未確認存在的容器:cats-inbox-api      ← test_skeleton.sh
❌ 🔴 staging 少了上游: cats-inbox-api                          ← test_gateway_staging.sh
```

portal 的白名單判準是「**VM 上已實測存在**」,而該註解自己寫著
「加進來卻沒驗過等於自欺」。`cats-inbox-api` 連容器都還不存在。

→ 改為**整段註解入庫** + 五步啟用順序。設定文字先進 repo 的價值不變
(低峰窗口只需解註解 + 套用),而**生效與否交給那兩道既有守門把關** ——
它們就是啟用檢查表本身,不必另外發明一個會被忘記的清單。

⚠ 計畫段裡「本次 PR 零影響」那一句反而**更強**了:註解 → nginx 讀不到 → 零位元組差異。

### 推翻 ②:我自己寫的啟用步驟第一版是壞的

第一版把設計理由也放在 `▼▲` 之間,啟用指示是「把每一行開頭的 `# ` 拿掉」。
**實際跑一次**機械式解註解,產出是:

```
    🔴 少了這一行,使用者打 `/inbox`(無尾斜線)會落到 `location /` → 入口首頁的 404。
    location = /inbox {
```

**說明文字變成了 nginx 指令。**

⚠ 這個錯誤是**安全的**(`ops/gateway-apply.sh` 的一次性容器會擋下),
但它**只會在低峰窗口顯現** —— 而拆成 T11a/T11b 的全部理由,就是不要在那個時間
處理這種東西。

→ 改法:說明一律寫在 `▼` 之前;`▼▲` 之間只留指令。
→ 並在 portal `tests/test_skeleton.sh` **加一條守門**釘住(109 → 110 項),
  因為下一個人很可能會「順手」在裡面補一行說明。

🔴 **這一項的意義**:它是本次唯一一個「**我寫的東西自己有 bug**」的發現,
而抓到它的不是審閱,是**真的執行了一次我寫給別人的步驟**。

---

## 結果段

**完工時間:** 2026-08-27 12:05 (UTC+8)

### 做了什麼(異動檔案清單)

| repo | 檔案 | 內容 |
|---|---|---|
| cats-portal | `gateway/nginx/conf.d/default.conf` | 新增 `/inbox/` 兩個 location —— 🔴 **整段註解**(見推翻①)+ 五步啟用順序;`proxy_pass` 無尾斜線、四個 header、零 add_header |
| cats-portal | `tests/test_skeleton.sh` | **新增守門**:`▼▲` 待啟用區塊之間只准有指令(109 → 110 項)|
| cats-portal | `DOCS/dev-logs/2026-08-27_cats-inbox路由以註解形式入庫.md` | 新增 —— ⚠ **檔頭明示**計畫段寫在本 repo,portal 側這篇是事後建立的,不偽裝流程跑過 |
| cats-portal | `DOCS/開發計畫書.md`(**v1.22**) | 新增 **§6.9 外部 App 路由掛載** —— 該文件先前**完全沒提過** cats-inbox 與 SENSE 這兩個「程式側完成、gateway 未掛載」的系統 |
| cats-portal | `CLAUDE.md`(**v1.21**) | 系統清單那列:「路由待其 T11 提 PR」→「**設定已入庫但整段是註解**」——兩者要做的下一件事完全不同 |
| cats-inbox | `docs/任務表.md` | T11 → **T11a ✅ / T11b ⬜** |

### 測試結果(紅 → 綠的證據)

| # | 測的東西 | 結果 |
|---|---|---|
| 1 | 設定直接生效 → portal 兩道既有守門 | 🔴 **紅**(推翻①);改為註解後綠 |
| 2 | 新守門「▼▲ 之間只准有指令」:把說明放回去 | 🔴 **紅**,訊息指名那一行 |
| 3 | 機械式解註解的產出 | ✅ 8 行純指令;整份設定大括號 **27/27 配對** |
| 4 | portal `bash tests/run-all.sh` | ✅ **57 支全綠**(`test_skeleton.sh` 109 → **110**) |
| 5 | cats-inbox `bash tests/run_all.sh`(起本機 PG) | ✅ **234 項全綠**(第三條4 安全網) |

🔴 **未跑過的**:`nginx -t`(本機無 nginx,已試 `apt-get install nginx` → 不可得)、
staging `verify.sh`、正式站套用 —— 三者都屬 **T11b**。

### 對現有資料的實際影響

🟢 **無。** 設定進 repo 不等於套用到正式站。

### 遺留問題與後續建議

1. ⏳ **T11b 的順序不可顛倒**(五步,詳見 portal `default.conf` 內):
   容器起在 `cats-edge` 並實測解析 + health → 解註解 → 加白名單(附實測證據)
   → staging 假上游 + `probe /inbox/abc cats-inbox-api /inbox/abc` + `verify.sh`
   → `ops/gateway-apply.sh`。
2. 🔴 **`proxy_pass` 不加尾斜線這一點只有推理,沒有實測。** 本機無 nginx。
   第 4 步的 staging `probe` 是驗它的東西 —— 它同時驗**送到哪個上游**與
   **上游收到什麼 URI**,而後者正是 portal 2026-08-10 第二次全平台停機的主題。
   ⚠ 寫錯的症狀是**全站 404 而 gateway 這一層完全正常**。
3. 💡 **`resolver 127.0.0.11` + 變數式 `proxy_pass`**(對齊指南 §3.2.1,portal 自標待辦):
   有它的話「上游沒起來」只會讓 `/inbox/` 回 502,**不會讓整份設定 `[emerg]`** ——
   本次那兩支紅燈的根因就會從紅線降級成小事。已寫進 portal 的 dev-log 作為建議。
4. ⏳ **PG 15 的 migration 演練仍未做**(本機只有 16.13)—— 一併在 T11b 於 VM 補。

---

## 版本歷史

| 版本 | 日期 | 修改人 | 摘要 |
|---|---|---|---|
| v1.1 | 2026-08-27 | Benny | T11a 完工:結果段 + 🔴 **計畫段被實測推翻的兩處**(第二條5,原文不刪)。**推翻①**:設定不能直接生效,只能**整段註解**入庫 —— portal 的上游白名單判準是「**VM 上已實測存在**」而 `cats-inbox-api` 連容器都沒有,加進去正是那段註解點名的**自欺**;兩道既有守門在我提交前就把我擋下來,而**生效與否交給它們把關**比另外發明一份清單可靠。**推翻②**:🔴 **我自己寫的啟用步驟第一版是壞的** —— `▼▲` 之間連說明一起註解,機械式解註解會把說明變成 nginx 指令 → 語法錯(安全,但**只會在低峰窗口顯現**,而拆成兩半的全部理由就是不要在那個時間處理這種事);已改為「說明寫在 ▼ 之前」並加一條 portal 守門釘住(109 → 110)。⚠ 抓到它的不是審閱,是**真的執行了一次我寫給別人的步驟**。portal 57 支全綠、cats-inbox 234 項全綠;對現有服務**零位元組差異**。🔴 誠實標註:`proxy_pass` 不加尾斜線**只有推理沒有實測**(本機無 nginx),由 T11b 的 staging `probe` 驗 |
| v1.0 | 2026-08-27 | Benny | T11a 計畫段(動工前寫)。🔴 **把 T11 拆成兩半**(第一條4):前半是純設定檔的 portal PR、後半要 VM 與低峰窗口 —— 合成一列會讓能做的跟著不能做的一起卡住,而低峰窗口當場想設定正是最不該發生的事。三個會靜默出錯的地方逐條記下(`proxy_pass` 帶尾斜線=全站 404 且與 `/upload/` 相反、靜態檔不必另開 location、本 location 零 `add_header` 否則掉上層標頭)。🔴 **刻意不把 `/inbox/` 加進 `gateway-apply.sh` 的五條驗證** —— 那是**自動回滾的觸發條件**,加進去等於讓一個還沒上線的新服務有權把 PLM/core 的改動整批回退。上游容器不存在會讓整份設定 `[emerg]` 而炸掉綁 80/443 的 gateway,故 T11b **順序不可顛倒**(工具會擋);根治的 `resolver` + 變數式 proxy_pass **本次不做**——無法在此環境 `nginx -t`,未實測的寫法不進正式設定檔 |
