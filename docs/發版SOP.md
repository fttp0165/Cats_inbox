# cats-inbox 發版 SOP

**建立日期:** 2026-08-25 17:05
**最後更新:** 2026-08-25 17:05
**版本:** v1.0
**適用範圍:** cats-inbox 的每一次發版(打 tag → 建映像 → VM 換版)
**權威依據:** 憲法**第八條**(發版儀式)+ 平台紅線(不用 `latest`、不在正式機建置)

> 本文把第八條變成一份**照著做就會對**的清單。
> 🔴 憲法寫了「content 必含四段」,而在本文之前沒有任何清單把它變成步驟
> —— 那條規則就只在有人記得的時候生效。

---

## 0. 現在還沒真的跑過哪些步驟(先講清楚)

🔴 **共通紅線:未實際跑過的不得宣稱通過。** 本 SOP 寫在 T10c,而當時:

| 步驟 | 狀態 |
|---|---|
| `release-image.yml` 的三道一致性檢查 | ⏳ **尚未**在真的 Actions 上跑過(本地無 docker daemon、無 GHCR 憑證) |
| 映像推上 GHCR | ⏳ **尚未**推過任何一版 —— `ghcr.io/fttp0165/cats-inbox-api:0.1.0` **目前不存在** |
| VM 上 `docker compose up -d` | ⏳ **尚未**(`/opt/cats-inbox` 尚不存在,T11) |
| gateway 路由與 reload | ⏳ **尚未**(路由權威在 portal,T11 提 PR) |

⚠ 也就是說:**第一次發版就是這份 SOP 的第一次演練**。第 1 步因此是「確認額度」,
而不是「打 tag」。

---

## 1. 發版前(在**開發者本機**)

【開發者本機】【repo 根,分支 `main`(或要發版的那條)】【cats-inbox】

```bash
# ① 確認 Actions 額度還夠 —— portal 的 CI 曾因額度用盡紅了一週(2026-08-24),
#    而那種故障的症狀是「發版當下才發現建不出來」。
#    在 GitHub UI:Settings → Billing → Actions 看剩餘分鐘數。

# ② 版本號常數改成要發的版(🔴 第八條4:必須在打 tag 之前改)
#    版本號會顯示給使用者(/inbox/health、頁尾),tag 與常數不一致等於說謊。
$EDITOR app/__init__.py          # __version__ = "0.2.0"
$EDITOR docker-compose.yml       # image: ghcr.io/fttp0165/cats-inbox-api:0.2.0

# ③ 全部測試跑過(🔴 含 migration 那 13 項 —— 要起本機 PG,
#    否則它們是 skipped 而不是 passed,而兩者在輸出上長得很像)
eval "$(bash tests/pg_local.sh start)"
bash tests/run_all.sh

# ④ 有 migration 的版本:本機 up → down → up 演練(第八條6)
#    沒有新 migration 的版本跳過這一步。
```

🔴 **②的兩個檔案必須一起改。** 只改常數而忘了 compose 的後果是
**部署的是舊映像**,而它的 health 回報**舊版本** —— 兩者「看起來完全一致」,
而你以為換版成功了。守門是 `tests/test_release.py::test_compose_image_tag_matches_version_constant`
與 workflow 裡的第①道檢查(它會讓建置直接失敗)。

---

## 2. 打 tag(在**開發者本機**或 **GitHub UI**)

【開發者本機】【repo 根,分支 `main`】【cats-inbox】

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

⚠ **這一節在 T11 完成前都用不到**(`/opt/cats-inbox` 尚不存在)。

【CATS VM(Ubuntu)】【`/opt/cats-inbox`】【cats-inbox】

```bash
# ① 改 tag 後 pull —— 🔴 不在正式機 `git pull && build`(平台紅線)
sudo sed -i 's|cats-inbox-api:.*|cats-inbox-api:0.2.0|' docker-compose.yml
sudo docker compose pull
sudo docker compose up -d

# ② 驗證(換版後的驗證點,對照 Release 的「部署注意」)
curl -s http://127.0.0.1:8080/inbox/health   # 容器內部;version 應為 0.2.0
```

🔴 **接著這一行動的是 portal 擁有的 gateway,不是本服務**:

【CATS VM(Ubuntu)】【`/opt/cats-portal`】【**cats-portal**】

```bash
sudo docker exec portal-gateway nginx -s reload
```

⚠ 本專案最容易貼錯的一組就是這兩段 —— 上面在 `/opt/cats-inbox`,
下面在 **portal 的**目錄、動的是綁 80/443 的**全 VM 唯一入口**。
**貼錯的代價是動到正式站,而錯誤不會當場顯現**(憲法第九條11)。

---

## 5. 發版前一分鐘檢查(第八條6)

| # | 檢查 | 不做的後果 |
|---|---|---|
| 1 | `__version__` 與 compose 的 tag 都改成本版了 | 部署舊映像而 health 回報舊版本,**看起來完全一致** |
| 2 | 測試全綠,且 migration 那 13 項**不是 skipped** | 「93 passed」與「全部跑過」在輸出上長得一樣 |
| 3 | 有 migration 的版本已本機 up→down→up | 回滾時才發現 downgrade 是空的 |
| 4 | Release 四段都寫了,且未跑過的如實標未跑過 | 讀的人拿它當現況,而它看起來很完整 |
| 5 | Actions 額度還夠 | 打了 tag 才發現建不出來,而 tag 不能重發 |

---

## 版本歷史

| 版本 | 日期 | 修改人 | 摘要 |
|---|---|---|---|
| v1.0 | 2026-08-25 | Benny | 初版(T10c)。把憲法第八條變成可照做的五節:發版前(改**兩個**檔案的版本號、跑含 PG 的全測試、migration 雙向演練)、打 tag(workflow 自動驗三道一致性)、寫 Release(四段模板逐段給例)、VM 換版(🔴 兩段指令分屬**兩個 repo 兩個目錄**,後者動的是全 VM 唯一的 gateway)、發版前一分鐘五項檢查。🔴 §0 誠實列出**目前還沒真的跑過**的四個步驟(workflow 沒在真 Actions 上跑過、映像從未推過、VM 目錄還不存在、gateway 路由未提 PR)——第一次發版就是這份 SOP 的第一次演練,故第 1 步是「確認額度」而不是「打 tag」 |
