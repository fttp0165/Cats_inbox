# 2026-08-27 T10d 映像缺 alembic → `alembic upgrade head` 無處可跑(v0.1.1)

**建立日期:** 2026-08-27 14:30
**最後更新:** 2026-08-27 15:30
**版本:** v1.1
**對應任務:** **T10d**(計畫外;T10c 交付的映像的缺陷,寫 T11b 指令稿時發現)

> 本篇計畫段於**動工前**寫妥(第二條2)。
> ⏱ 時間戳來源:開發機 `date` = `2026-08-27 06:30 UTC` → 本檔一律標 UTC+8。

---

## 計畫段(動工前寫妥,第二條2)

### 🔴 發現的過程:要寫「可貼的指令稿」才發現有一條指令貼不出來

Benny 要 T11b 的可貼指令稿。寫到「跑 migration」那一步時,我去找那行指令
**應該在哪裡下**,結果是:**沒有地方可以下。**

```dockerfile
COPY requirements.txt .                        # builder 段
COPY --from=builder /install /usr/local
COPY --chown=appuser:appuser app/ ./app/       # ← 只有 app/
```

映像裡**沒有 `alembic.ini`,也沒有 `alembic/versions/`**。
`alembic` 這個套件本身有(在 `requirements.txt` 裡),但**沒有設定檔也沒有 migration 腳本**
—— `docker exec cats-inbox-api alembic upgrade head` 會失敗在「找不到 alembic.ini」。

而同時:

| 事實 | 出處 |
|---|---|
| 應用**刻意不做** `create_all()`,schema 的唯一權威是 `alembic/versions/` | `app/db.py` 檔頭、`app/main.py:86` |
| 「**要跑 migration:是 —— `alembic upgrade head`**」 | **v0.1.0 的 Release 頁**,「部署注意」段 |
| 平台紅線:**不在正式機 `git pull && build`** | 憲法附則 |

🔴 **三件事合起來的意思是:照 Release 頁寫的做不到,而三份文件都對。**
這與 T10c 的發現**完全同型** —— 那次是「compose 引用一個沒有任何 workflow 會建的映像」,
這次是「Release 頁要求跑一個映像裡不存在的東西」。
**每份文件都對、每個任務也都完成了,而把它們接起來的那一步不在任何人的清單上。**

### 症狀為什麼特別難查

容器會**正常啟動**、`/inbox/health` 會回 **200**(健康檢查刻意不查 DB,T01 的設計)。
🔴 所以 T11b 的第 1 步「容器起來 + health 200」會**通過**,gateway 也會套用成功,
而**第一個真人打開收件匣時才 500**(表不存在)。
⚠ 那個時刻是低峰窗口**之後**,而且 gateway 已經套好了 —— 排查的人會先去看 gateway。

### 處置

| # | 改什麼 | 為什麼 |
|---|---|---|
| 1 | `Dockerfile` 加 `COPY alembic.ini` 與 `COPY alembic/` | 讓 `docker exec … alembic upgrade head` 真的有東西可跑 |
| 2 | 版本 `0.1.0` → **`0.1.1`**(`app/__init__.py` + `docker-compose.yml`)| 🔴 第八條5:**同一個 tag 不重發**。`0.1.0` 已推上 GHCR,只能發下一個 patch |
| 3 | `tests/test_release.py` **加守門** | 這個缺口**沒有任何測試會發現**——現有測試都從 repo 跑 alembic,從不從映像跑 |
| 4 | `docs/發版SOP.md` 加一節「換版後要不要跑 migration」| SOP 目前只說「要跑就寫指令」,沒說**在哪裡下** |

### ⚠ 一個我**不做**的選項:啟動時自動 migrate

`entrypoint` 裡跑 `alembic upgrade head` 再起 uvicorn,看起來更省事。**不做**,兩個理由:

1. 🔴 **多副本時會有兩個 process 同時 migrate**(現在只有一副本,但這是預設值問題);
2. 🔴 更重要:**上線後動資料庫的操作必須「動手前先備份」**(共通紅線)。
   自動 migrate 會讓 schema 變更在**沒有人按下確認**的情況下發生,
   而 `docker compose up -d` 是一個平常到不會有人特別注意的指令。
   → migration 保持**顯式**的一步,由人在備份之後執行。

### 影響範圍

| 對象 | 影響 |
|---|---|
| 應用程式邏輯 / API / UI | 🟢 **完全不動**(只多帶兩份檔案進映像)|
| 映像 | 🟡 **內容變更 → 必須發 `0.1.1`**;`0.1.0` 留在 GHCR 不動(不可重發)|
| 資料庫 | 🟢 本次不動;**但這正是讓 T11b 能動它的前提** |
| `docs/發版SOP.md`、`docs/進度表.md`、任務表 | 🟡 回寫 |

### 驗收標準

1. `Dockerfile` 帶入 `alembic.ini` 與 `alembic/`,擁有者 `appuser`。
2. `tests/test_release.py` **新增守門**:Dockerfile 必須 COPY 這兩者
   —— 並**實測「把 COPY 拿掉 → 紅」**。
3. 版本常數與 compose 的 tag **同時**改為 `0.1.1`(現有守門
   `test_compose_image_tag_matches_version_constant` 會驗)。
4. `docs/發版SOP.md` 新增「migration 在哪裡下」;升版。
5. `bash tests/run_all.sh`(起本機 PG)全綠。
6. ⚠ **不由我推 tag**:第八條5 的不可逆性 + v0.1.0 的先例是 Benny 推。
   我備妥 tag 指令與 Release 四段內容,放進 T11b 指令稿的第 0 步。

### 回滾方式

`git revert`。⚠ 若 `0.1.1` 已推上 GHCR,revert 程式碼**不會**收回映像 ——
回滾方式是把 compose 的 tag 改回 `0.1.0`,而**那個映像跑不了 migration**
(也就是回到現在這個狀態)。

---

## 結果段

**完工時間:** 2026-08-27 14:55 (UTC+8)

### 做了什麼(異動檔案清單)

| 檔案 | 內容 |
|---|---|
| `Dockerfile` | 新增 `COPY --chown=appuser:appuser alembic.ini ./` 與 `alembic/ ./alembic/`,附「為什麼映像要帶 migration」註釋 |
| `app/__init__.py` | `__version__` `0.1.0` → **`0.1.1`** |
| `docker-compose.yml` | 映像 tag → **`0.1.1`**(🔴 與常數同一次改)|
| `tests/test_release.py` | **新增兩條守門**:①Dockerfile 必須帶 `alembic.ini` 與 `alembic/`;②**反向**——不得在 `CMD`/`ENTRYPOINT` 裡自動 migrate |
| `docs/發版SOP.md`(**v1.3**) | 新增 §4.1「migration 在哪裡下」(含備份指令)|
| `docs/進度表.md`(**v1.1**)、`docs/任務表.md`(**v1.23**) | 回寫 |

### 為什麼這樣做(關鍵決策)

**① 帶進映像,而不是在 VM 上放一份 repo。**
平台紅線是「不在正式機 `git pull && build`」;VM 上只有 compose 與 `.env`。
把 migration 腳本放進映像,等於讓「**這個映像**知道自己的 schema 該長什麼樣」
—— 版本與 migration 綁在一起,不會出現「映像是 0.1.1 而腳本是別的版本」。

**② 加一條反向守門:禁止在 `CMD`/`ENTRYPOINT` 自動 migrate。**
🔴 光是「帶進去」會誘惑下一個人順手改成自動執行(那看起來更方便)。
而自動 migrate 會讓 schema 變更在**沒有人按下確認**的情況下發生 ——
而共通紅線要求「上線後動資料庫前必先備份」。
`docker compose up -d` 平常到不會有人特別注意,**它不該是一個會改 schema 的指令**。

**③ 發 `0.1.1` 而不是重推 `0.1.0`。** 第八條5。`0.1.0` 的映像留在 GHCR ——
它不是壞的,只是**跑不了 migration**;誠實記錄比讓它消失好。

### 測試結果(紅 → 綠的證據)

| # | 測的東西 | 結果 |
|---|---|---|
| 1 | 新守門對**當時的** Dockerfile | 🔴 **紅**:「Dockerfile 沒有把 `alembic.ini` 帶進映像 —— alembic 找不到設定檔」 |
| 2 | 加上兩行 `COPY` 之後 | ✅ 綠 |
| 3 | 反向守門:把 `alembic upgrade head` 塞進 `CMD` | 🔴 **紅**:「啟動指令裡有 alembic —— migration 必須是顯式的一步(備份之後才做)」 |
| 4 | 版本一致性(既有守門)| ✅ 綠 —— `__version__` 與 compose tag **同時**是 `0.1.1` |
| 5 | `tests/test_release.py` | 10 → **12** 支 |
| 6 | `bash tests/run_all.sh`(起本機 PG)| ✅ 文件層 **64 過 / 0 敗**、pytest **183 passed / 0 skipped** = **247 項全綠** |

🔴 **未跑過的**:`docker build` 本身(本環境無 docker daemon)——
第一次真的建置是推 `v0.1.1` 之後的 `release-image` workflow,
而它的冒煙步驟會把映像**跑起來**驗 health 與 non-root。
⚠ 但**它不會驗 alembic 跑不跑得動** —— 那要等 T11b 的 B-3。
💡 建議:下一次動 workflow 時,在冒煙步驟加一行
`docker exec smoke alembic --version && docker exec smoke test -f alembic.ini`
(不需要資料庫,只驗檔案在不在)。

### 對現有資料的實際影響

🟢 **本次不動任何資料。** ⚠ 但它是讓 T11b **能夠**動資料(建表)的前提。

### 遺留問題與後續建議

1. ⏳ **`v0.1.1` 的 tag 未推。** 指令與 Release 四段內容備妥在
   `docs/T11b上線指令稿.md` 第 0 步;⚠ **不由我推**(第八條5 的不可逆性
   + `v0.1.0` 的先例是 Benny 推)。
2. 🔴 **workflow 的冒煙步驟驗不到這個缺口。** 它驗 health 與 non-root,
   而映像**少了 alembic 一樣會通過** —— 建議加兩行檔案存在性檢查(見上)。
   ⚠ 不在本次範圍:那要改 workflow,而 workflow 只在推 tag 時跑,
   改它應該與下一次真的要發版時一起做,不然驗不到自己改對沒有。
3. ⚠ **`0.1.0` 留在 GHCR 不動。** 它不是壞的,只是**跑不了 migration**;
   誠實記錄比讓它消失好(而第八條5 本來就不允許重發)。

---

## 版本歷史

| 版本 | 日期 | 修改人 | 摘要 |
|---|---|---|---|
| v1.1 | 2026-08-27 | Benny | T10d 完工:結果段。`Dockerfile` 帶入 `alembic.ini` 與 `alembic/`;版本常數與 compose tag **同時**升 **0.1.1**;`tests/test_release.py` 10 → **12** 支;`發版SOP.md` **v1.3** 新增 §4.1「migration 在哪裡下」(先備份 → `alembic upgrade head` → `\dt` 驗五張表)。**兩條守門先紅後綠**:拿掉 `COPY alembic.ini` → 紅、把 `alembic upgrade head` 塞進 `CMD` → 紅。247 項全綠。🔴 **一項誠實標註**:`docker build` 本身沒跑過(本環境無 docker daemon),而 workflow 的冒煙步驟**驗不到這個缺口** —— 它驗 health 與 non-root,而**映像少了 alembic 一樣會通過**;已建議下次動 workflow 時加兩行檔案存在性檢查,但不在本次範圍(workflow 只在推 tag 時跑,改它驗不到自己改對沒有)。三項遺留(`v0.1.1` tag 未推、workflow 冒煙有盲點、`0.1.0` 留在 GHCR 不動)|
| v1.0 | 2026-08-27 | Benny | T10d 計畫段(動工前寫)。🔴 **發現於「要寫可貼的指令稿,才發現有一條指令貼不出來」**:`Dockerfile` 只 `COPY app/`,映像裡**沒有 `alembic.ini` 也沒有 `alembic/versions/`**,而應用刻意不 `create_all()`、v0.1.0 的 Release 頁又明寫「要跑 migration:是 —— `alembic upgrade head`」、平台紅線禁止在正式機 build —— **三份文件都對,而照 Release 頁做不到**。與 T10c **完全同型**(那次是 compose 引用一個沒有任何 workflow 會建的映像)。🔴 **症狀特別難查**:健康檢查刻意不查 DB,所以容器會正常啟動、health 200、gateway 套用成功,**第一個真人打開收件匣時才 500**,而那時 gateway 已經套好了,排查的人會先去看 gateway。處置四項(Dockerfile 帶入、版本升 **0.1.1** 因為 `0.1.0` 已推而第八條5 不得重發、加守門因為**現有測試都從 repo 跑 alembic 從不從映像跑**、SOP 補「在哪裡下」)。⚠ **刻意不做啟動時自動 migrate**:多副本會同時 migrate,更重要的是共通紅線要求動資料庫前必先備份,而自動 migrate 會讓 schema 變更在沒有人按下確認的情況下發生,而 `docker compose up -d` 平常到不會有人特別注意 |
