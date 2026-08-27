# cats-inbox 發版 SOP

**建立日期:** 2026-08-25 17:05
**最後更新:** 2026-08-27 17:10
**版本:** v1.4
**適用範圍:** cats-inbox 的每一次發版(打 tag → 建映像 → VM 換版)
**權威依據:** 憲法**第八條**(發版儀式)+ 平台紅線(不用 `latest`、不在正式機建置)

> 本文把第八條變成一份**照著做就會對**的清單。
> 🔴 憲法寫了「content 必含四段」,而在本文之前沒有任何清單把它變成步驟
> —— 那條規則就只在有人記得的時候生效。

---

## 0. 哪些步驟真的跑過了、哪些還沒(先講清楚)

🔴 **共通紅線:未實際跑過的不得宣稱通過。** 本表逐列標狀態,**不留空白**
——空白會被讀成「應該沒問題」。

| 步驟 | 狀態 |
|---|---|
| `release-image.yml` 的三道一致性檢查 | ✅ **已跑過兩次**(`#1` 2026-08-26 79 秒、`#2` 2026-08-27 60 秒,皆 8 步全過) |
| 映像推上 GHCR | ✅ **已推兩版** —— `0.1.0`(`release-image #1`,79 秒)與 **`0.1.1`**(`#2`,60 秒);`tags/list` 回 `["0.1.0","0.1.1"]`,**沒有 `latest`** |
| VM 上 `docker compose up -d` | ⏳ **尚未**(`/opt/cats-inbox` 尚不存在,**T11b**;指令稿見 `docs/T11b上線指令稿.md`) |
| gateway 路由與 reload | ⏳ **尚未** —— 設定**已入庫但整段是註解**(T11a ✅);啟用是 **T11b** |

⚠ 前兩列在 T10c 當時都是 ⏳,而 **Benny 2026-08-26 19:28 推 `v0.1.0` 之後 82 秒
它們就變成假的** —— 回寫紀錄見 `docs/dev-logs/2026-08-26_D03_首次發版實測與回寫.md`。
🔴 **後兩列仍然沒跑過**,所以第 4 節到 T11 完成前都用不到。

---

## 1. 發版前(在**開發者本機**)

**① 確認 Actions 額度還夠**

> 🖥️ **在哪執行:** GitHub UI(不是指令)

portal 的 CI 曾因額度用盡紅了一週(2026-08-24),而那種故障的症狀是
**發版當下才發現建不出來**。看 Settings → Billing → Actions 的剩餘分鐘數。

**② 改版本號常數**(🔴 第八條4:必須在打 tag 之前改)

版本號會顯示給使用者(`/inbox/health`、頁尾),tag 與常數不一致等於說謊。
🔴 **兩個檔都要改**,而它們在不同的目錄層級:

> 🖥️ **在哪執行:** WSL(Ubuntu)· 工作目錄 ~/Cats_inbox · 分支 main
> 📄 **編輯哪個檔:** ~/Cats_inbox/app/__init__.py  → `__version__ = "0.2.0"`
> 📄 **編輯哪個檔:** ~/Cats_inbox/docker-compose.yml → `image: ghcr.io/fttp0165/cats-inbox-api:0.2.0`

**③ 全部測試跑過**(🔴 含 migration 那 13 項 —— 要起本機 PG,否則它們是
skipped 而不是 passed,而兩者在輸出上長得很像)

> 🖥️ **在哪執行:** WSL(Ubuntu)· 工作目錄 ~/Cats_inbox · 分支 main

```bash
eval "$(bash tests/pg_local.sh start)"
bash tests/run_all.sh
```

**④ 有 migration 的版本:本機 up → down → up 演練**(第八條6)。
沒有新 migration 的版本跳過這一步。

🔴 **②的兩個檔案必須一起改。** 只改常數而忘了 compose 的後果是
**部署的是舊映像**,而它的 health 回報**舊版本** —— 兩者「看起來完全一致」,
而你以為換版成功了。守門是 `tests/test_release.py::test_compose_image_tag_matches_version_constant`
與 workflow 裡的第①道檢查(它會讓建置直接失敗)。

---

## 2. 打 tag(在**開發者本機**或 **GitHub UI**)

> 🖥️ **在哪執行:** WSL(Ubuntu)· 工作目錄 ~/Cats_inbox · 分支 main

```bash
git tag -a v0.2.0 -m "v0.2.0"
git push origin v0.2.0
```

推上去之後 `release-image.yml` 會自動:

1. 驗 **git tag == `__version__` == compose 的 tag**(任一不符即失敗);
2. 建映像、**先跑起來**驗 `/inbox/health` 回報的版本與 non-root;
3. 推 `ghcr.io/fttp0165/cats-inbox-api:0.2.0`。

🔴 **不會推 `latest`。** `latest` 讓同一個 tag 在不同時間指向不同東西,
回滾時你不知道要回到哪裡。
⚠ 建置失敗時**不要重推同一個 tag**(第八條5)——修好之後用
`workflow_dispatch` 手動補建,或發下一個 patch 版。

⏱ **實測耗時(v0.1.0,`release-image #1`):整趟 79 秒** ——
建置 42s、冒煙 2s、推送 7s,其餘是 setup。額度上這是一次可以接受的花費。
🔴 **綠燈看不出來的兩件事,要離開 GitHub 才驗得到**(v0.1.0 都實測過):

```bash
# 在任何有網路的機器上;不需要登入,也不需要 docker
TOK=$(curl -s "https://ghcr.io/token?scope=repository%3Afttp0165%2Fcats-inbox-api%3Apull&service=ghcr.io" \
      | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
curl -s -H "Authorization: Bearer $TOK" https://ghcr.io/v2/fttp0165/cats-inbox-api/tags/list
```

① **有沒有誤推 `latest`** —— workflow success 不會告訴你,要問 registry
(v0.1.0 實測:`{"tags":["0.1.0"]}`);
② **別人拉不拉得到** —— 見第 4 節開頭。

---

## 3. 寫 Release(在 **GitHub UI**)

**title 格式:** `vX.Y.Z — 一句話重點`

**content 必含四段,缺一不可**(第八條3):

```markdown
## 本版內容
- T09:公告(發布 / 有效期 / active / 逐人已讀)
- T10:安全紅線落地(跳脫 / action_url 白名單 / 嚴格 CSP)

## 對現有資料的影響
🟢 不動 / 🟡 加欄位 / 🔴 UPDATE 或刪除 —— 挑一個,並明講**有無 migration**。
例:🟡 加欄位;有 migration `0003_xxx`,已在本機 up→down→up 演練過。

## 部署注意
- `.env` 是否新增變數(新增了就列出變數名,**不列值**)
- 是否需跑 migration(要跑就寫指令)
- 換版後的驗證點(例:`curl -skI https://catsapp.sporton.com.tw/inbox/health` 回 200
  且 `version` 等於本版)

## 測試
本版測試總數、CI 綠燈狀態。
🔴 未實際跑過的不得寫成通過;skipped 要如實寫成 skipped。
```

🔴 **Release 頁是使用者與未來的自己唯一會看的「這一版是什麼」** —— tag 本身
說不出任何事。

---

## 4. VM 換版(在 **CATS VM**)

⚠ **這一節在 T11b 完成前都用不到**(`/opt/cats-inbox` 尚不存在)。**首次上線走 `docs/T11b上線指令稿.md`**,本節是之後每次換版用的。

✅ **VM 端不需要 `docker login ghcr.io`。** 2026-08-26 實測:以**匿名** token 打
`ghcr.io/v2/fttp0165/cats-inbox-api/manifests/0.1.0` 回 **HTTP 200**(repo 為 public,
套件跟著公開)。
🔴 **這件事值得專門驗一次**:GHCR 的**新套件預設是 private**,而 private 的症狀是
VM 上 `docker compose pull` 回 `unauthorized` —— 出現的時機與 T10c 想擋掉的
`manifest unknown` 一模一樣(**低峰窗口、gateway 正等著 reload**),而
「要先 `docker login`」這一步當時不在任何一份部署文件裡。
⚠ 哪一天套件被改成 private,這一節就要加回 `docker login`;判斷方式是第 2 節那段 curl。

> 🖥️ **在哪執行:** Cats VM(ssh 進去)· 工作目錄 /opt/cats-inbox
> 📄 **編輯哪個檔:** /opt/cats-inbox/docker-compose.yml(把映像 tag 改成本版)

```bash
# ① 改 tag 後 pull —— 🔴 不在正式機 `git pull && build`(平台紅線)
sudo sed -i 's|cats-inbox-api:.*|cats-inbox-api:0.2.0|' docker-compose.yml
sudo docker compose pull
sudo docker compose up -d

# ② 驗證(換版後的驗證點,對照 Release 的「部署注意」)
curl -s http://127.0.0.1:8080/inbox/health   # 容器內部;version 應為 0.2.0
```

🔴 **接著這一行動的是 portal 擁有的 gateway,不是本服務**:

> 🖥️ **在哪執行:** Cats VM(ssh 進去)· 工作目錄 /opt/cats-portal
> 🏷️ **動到誰的東西:** cats-portal(全 VM 唯一的 gateway,綁 80/443)

```bash
sudo docker exec portal-gateway nginx -s reload
```

⚠ 本專案最容易貼錯的一組就是這兩段 —— 上面在 `/opt/cats-inbox`,
這一段在 **portal 的**目錄、動的是綁 80/443 的**全 VM 唯一入口**。
🔴 這個指令的**工作目錄根本不重要**,「動到誰的東西」那一行才是全部的重點
——這就是憲法**第九條13** 存在的理由。
**貼錯的代價是動到正式站,而錯誤不會當場顯現。**

---

## 4.1 migration 在哪裡下(T10d 之後才成立)

🔴 **`0.1.0` 的映像跑不了 migration** —— `Dockerfile` 當時只 `COPY app/`,
映像裡沒有 `alembic.ini` 也沒有 `alembic/versions/`,而 `0.1.0` 的 Release 頁
卻明寫「要跑 migration:是 —— `alembic upgrade head`」。
**`0.1.1` 起映像自己帶著 migration 腳本**,所以下面這一段才成立。

⚠ 症狀為什麼難查:健康檢查刻意不查 DB,所以容器會正常啟動、`/inbox/health` 回 200、
gateway 也套用成功 —— **第一個真人打開收件匣時才 500**(表不存在)。

**① 先備份**(🔴 共通紅線:上線後動資料庫前必先備份;首次建表時庫是空的,
但這一步的紀律不因「這次沒資料」而豁免):

> 🖥️ **在哪執行:** Cats VM(ssh 進去)· 工作目錄 /opt/cats-inbox

```bash
sudo docker exec cats-inbox-pg pg_dump -U cats_inbox cats_inbox \
  > ~/inbox-before-$(date +%Y%m%d-%H%M).sql
```

**② 跑 migration**(顯式的一步,**不在容器啟動時自動跑** —— 見 Dockerfile 內註釋):

```bash
sudo docker exec cats-inbox-api alembic upgrade head
sudo docker exec cats-inbox-api alembic current   # 應顯示最新 revision
```

**③ 驗表真的在**:

```bash
sudo docker exec cats-inbox-pg psql -U cats_inbox -d cats_inbox -c '\dt'
# 期望看到:app_user / user_role / message / announcement / announcement_read
#           + alembic_version
```

🔴 **沒有新 migration 的版本跳過本節。** 判斷方式:`alembic current` 的輸出
已經等於 `alembic heads`。

---

## 5. 發版前一分鐘檢查(第八條6)

| # | 檢查 | 不做的後果 |
|---|---|---|
| 1 | `__version__` 與 compose 的 tag 都改成本版了 | 部署舊映像而 health 回報舊版本,**看起來完全一致** |
| 2 | 測試全綠,且 migration 那 13 項**不是 skipped** | 「93 passed」與「全部跑過」在輸出上長得一樣 |
| 3 | 有 migration 的版本已本機 up→down→up | 回滾時才發現 downgrade 是空的 |
| 4 | Release 四段都寫了,且未跑過的如實標未跑過 | 讀的人拿它當現況,而它看起來很完整 |
| 5 | Actions 額度還夠 | 打了 tag 才發現建不出來,而 tag 不能重發 |
| 6 | 🔴 **Release 頁真的建出來了**(不是「內容寫好了」而是**頁面存在**)| **裸 tag 的狀態會持續**,而第八條1 明訂不得只推裸 tag。⚠ 2026-08-27 實測發生過一次:tag 推成功、CI 綠、映像上了 GHCR,而 Release 頁不存在(API 查回 404)—— 查法:`https://github.com/fttp0165/Cats_inbox/releases/tag/vX.Y.Z` 打不開就是沒建 |

---

## 版本歷史

| 版本 | 日期 | 修改人 | 摘要 |
|---|---|---|---|
| v1.4 | 2026-08-27 | Benny | **§0 前兩列更新為兩次實測**:三道一致性檢查已跑過 `#1`(79 秒)與 `#2`(60 秒);映像**已推兩版** `0.1.0` 與 `0.1.1`,`tags/list` 回 `["0.1.0","0.1.1"]` **無 `latest`**。⚠ 順帶記一個 SOP 本身的教訓(已落到 `T11b上線指令稿` v1.3):第 2 節「打 tag」與第 3 節「寫 Release」**是兩節**,而走本機路徑的人推完 tag 就會停 —— **裸 tag 的狀態會持續**,而第八條1 明訂不得只推裸 tag。2026-08-27 實測發生過一次 |
| v1.3 | 2026-08-27 | Benny | **新增 §4.1「migration 在哪裡下」(T10d)**。🔴 本節補的是一個**沒有地方可以下那行指令**的缺口:`0.1.0` 的 `Dockerfile` 只 `COPY app/`,映像裡**沒有 `alembic.ini` 也沒有 `alembic/versions/`**,而 `0.1.0` 的 Release 頁卻明寫「要跑 migration:是 —— `alembic upgrade head`」;應用又刻意不做 `create_all()`、平台紅線禁止在正式機 build —— **三份文件都對,而照 Release 頁做不到**。`0.1.1` 起映像自帶 migration 腳本,本節才成立。三步:先 `pg_dump` 備份(共通紅線,不因「這次沒資料」而豁免)→ `docker exec … alembic upgrade head` → `psql -c '\\dt'` 驗五張表真的在。⚠ 症狀難查的理由一併寫進去:健康檢查刻意不查 DB,所以容器正常啟動、health 200、gateway 套用成功,**第一個真人打開收件匣時才 500** |
| v1.2 | 2026-08-26 | Benny | **首次發版實測回寫(D03)**。§0 前兩列 ⏳ → ✅:`release-image #1`(run `32963503452`)**79 秒全過**、映像 `ghcr.io/fttp0165/cats-inbox-api:0.1.0` 已在 GHCR(digest `sha256:90d5a6f9…`);🔴 **後兩列仍然是 ⏳**(VM 部署、gateway 路由),節標題由「現在還沒真的跑過哪些步驟」改為「哪些真的跑過了、哪些還沒」並要求**逐列標狀態不留空白**(空白會被讀成「應該沒問題」)。§2 新增**離開 GitHub 才驗得到的兩件事**與可直接貼的匿名 curl:①有沒有誤推 `latest`(workflow success 不會告訴你,v0.1.0 實測 `{"tags":["0.1.0"]}`)②別人拉不拉得到。§4 新增 ✅ **VM 端不需要 `docker login`**(匿名 manifest 回 200)—— 🔴 這件事值得專門驗一次,因為 **GHCR 新套件預設 private**,而 private 的症狀是 `docker compose pull` 回 `unauthorized`,出現時機與 `manifest unknown` 一模一樣(低峰窗口、gateway 等著 reload),而「要先 login」當時不在任何部署文件裡 |
| v1.1 | 2026-08-26 | Benny | **改用憲法 v1.5 的新指令位置格式**(D02)。五處指令區塊由 `【機器】【路徑】【專案】` 改為 `> 🖥️ 在哪執行` 引言;🔴 §1 原本把要改的檔藏在指令裡(`$EDITOR app/__init__.py`)—— 現在改成**兩行 `📄 編輯哪個檔` 明列完整路徑**,因為相對路徑要讀的人自己推,而推錯不會有錯誤訊息(改到另一個 repo 裡同名的檔,測試照樣綠);§4 的 gateway reload 改用**第九條13 的 `🏷️ 動到誰的東西`** —— 那個指令的工作目錄根本不重要,而動的是 portal 擁有的全 VM 唯一入口 |
| v1.0 | 2026-08-25 | Benny | 初版(T10c)。把憲法第八條變成可照做的五節:發版前(改**兩個**檔案的版本號、跑含 PG 的全測試、migration 雙向演練)、打 tag(workflow 自動驗三道一致性)、寫 Release(四段模板逐段給例)、VM 換版(🔴 兩段指令分屬**兩個 repo 兩個目錄**,後者動的是全 VM 唯一的 gateway)、發版前一分鐘五項檢查。🔴 §0 誠實列出**目前還沒真的跑過**的四個步驟(workflow 沒在真 Actions 上跑過、映像從未推過、VM 目錄還不存在、gateway 路由未提 PR)——第一次發版就是這份 SOP 的第一次演練,故第 1 步是「確認額度」而不是「打 tag」 |
