# cats-inbox TDD 測試計畫表

**建立日期:** 2026-08-15 09:40
**最後更新:** 2026-08-25 16:20
**版本:** v1.10

> 依 `docs/開發計畫書.md` §3 的架構逐元件展開:**每個任務動工前該先寫哪一支會失敗的測試**。
> 憲法第三條:先寫紅測試釘住預期行為,才改程式;「測試全綠」指**實際跑過看到綠燈**。
> 本表與 `docs/任務表.md` 一一對應(同一組 Tnn 編號);任務表管「做什麼、何時做」,本表管「怎麼證明它真的做到了」。

---

## 0. TL;DR

1. **跑全部測試:** `bash tests/run_all.sh`(CI 同一入口;新增 `tests/test_*.sh` 或 `tests/test_*.py` 會自動被撿起)。
2. **裝測試依賴:** `pip install -r requirements-dev.txt`。
3. **循環:** 寫紅測試 → 跑到**看見它失敗** → 寫最小實作 → 跑到綠 → 把紅/綠輸出貼進該任務 dev-log。
4. **現況:** 44 項全綠(文件層 21 + 骨架層 19 + 應用層 4);M2 起的測試尚未撰寫,本表 §3 列出每一支該長什麼樣。
5. **不可放水:** CI 紅燈一律照設計改回;唯測試斷言本身有誤才修測試,並於 dev-log 說明。

---

## 1. 測試分層

| 層 | 檔案 | 跑什麼 | 為什麼放這層 |
|---|---|---|---|
| **文件層** | `tests/test_docs.sh`(bash) | metadata/版本歷史、md+HTML 並存、light 主題、零外部 CDN、md↔HTML 未漂移 | 文件不合格不會有錯誤訊息,只會在幾週後變成「分不清哪份是現況」 |
| **骨架層** | `tests/test_skeleton.py`(pytest) | compose 結構、網路隔離、port 暴露面、映像版本、Dockerfile non-root、`.env.example` 完整性 | 這些錯誤在**檔案內容**就決定了;釘在檔案層才能在 PR 就爆,而不是 VM 上部署後才 502 |
| **應用層** | `tests/test_app.py`(pytest) | 端點行為、權限判定、輸入驗證、token 驗證 | 業務正確性的主戰場;M2 起會拆成 `test_auth.py`/`test_messages.py`/`test_push.py` |
| **手動冒煙** | SSO 契約 §7 清單 | 真 IdP 的登入/登出/SLO、經 gateway 的路由 | 需要真的 Keycloak 與 gateway,CI 跑不動;**未完成項如實打叉**,不得略過 |

🔴 **一條紀律:找不到 pytest 一律視為失敗,不靜默跳過**(`run_all.sh` 已如此實作)。
「沒跑」與「跑過且綠」在輸出上長得一樣的話,共通紅線的第一條就形同虛設。

---

## 2. 架構元件 → 測試對應

<!--SVG:cats-inbox_架構總覽-->

```
元件(開發計畫書 §3.1)              釘住它的測試
────────────────────────────────────────────────────────────────
portal-gateway → /inbox/           test_skeleton.py(alias/expose)+ T11 手動 curl
cats-inbox-api 容器                test_skeleton.py(non-root/8080/healthcheck/restart)
cats-inbox-pg(PG15,內部網)       test_skeleton.py(不上 cats-edge、無 ports、具名 volume)
Keycloak OIDC(身分)              test_auth.py(T04:RS256/aud/exp/±30s/續期)
訊息(業務)                        test_inbox.py(T08:deny-by-default/零人名)
公告(業務)                        test_announcements.py(T09:權限/有效期/逐人已讀)
輸入驗證 / 跳脫 / CSP                test_security.py(T10:14 則反例、nonce、log)
S2S 推送(系統通知)                test_push.py(T14:無 token/錯 audience → 401)
front-channel logout(SLO)         test_auth.py(T06:免認證、冪等、只刪 cookie)
```

---

## 3. 逐任務 TDD 計畫

### M1 骨架

| 任務 | 紅測試(檔案::案例) | 斷言(預期的失敗) | 轉綠條件 | 狀態 |
|---|---|---|---|---|
| T01 | `test_app.py::test_health_returns_200_without_database` | DB 指向不存在主機時 `/inbox/health` 仍須 200——**「沒查 DB」與「剛好查得到」在正常環境下觀測結果相同,把 DB 弄壞是唯一能分辨的方法** | health 不觸碰任何 DB 連線 | ✅ |
| T01 | `test_app.py::test_health_version_matches_constant` | 回應的 `version` != `app.__version__` 即失敗 | 版本號單一來源(第八條4) | ✅ |
| T01 | `test_app.py::test_routes_are_mounted_under_inbox_prefix` | `/health`(無前綴)必須 404 | 路由統一掛 `/inbox` 前綴 | ✅ |
| T01 | `test_app.py::test_no_secret_defaults_in_config` | secret 類設定不得有可用預設值 | fail-closed:未設定就是空 | ✅ |
| T01 | `test_skeleton.py::test_pg_not_on_cats_edge` | `cats-inbox-pg` 出現在 `cats-edge` 即失敗 | DB 只在自家內部網 | ✅ |
| T01 | `test_skeleton.py::test_no_service_publishes_ports` | 任一服務有 `ports:` 即失敗 | 只 expose 不 publish | ✅ |
| T01 | `test_skeleton.py::test_cats_edge_declared_external_with_name` | 缺 `external: true` 或 `name: cats-edge` 即失敗 | 對齊指南 §2.2 零歧義寫法 | ✅ |
| T01 | `test_skeleton.py::test_postgres_is_15` / `test_no_latest_tag` / `test_no_build_directive` | 版本紅線三連 | PG15、釘 tag、prebuilt | ✅ |
| T01 | `test_skeleton.py::test_dockerfile_runs_as_non_root` | 最後的 `USER` 是 root 或缺 `USER` 即失敗 | non-root(UID 1000) | ✅ |
| T01 | `test_skeleton.py::test_env_example_lists_every_variable_read_by_config` | 掃 `config.py` 的變數名比對 `.env.example`,缺一即失敗 | 缺漏=CI 失敗(基礎設施紅線) | ✅ |

### M2 SSO 接入(測試待寫,規格如下)

| 任務 | 紅測試 | 斷言 | 為什麼這支測試非有不可 |
|---|---|---|---|
| T04 | `test_auth.py::test_rejects_hs256_and_none_alg` | `alg=HS256`/`none` 的 token → **401** | 契約 §3.2 只接受 RS256;放行等於任何人可自簽身分 |
| T04 | `test_auth.py::test_rejects_wrong_audience` | `aud` 非本 client_id → 401 | 防拿 A app 的 token 打 B app |
| T04 | `test_auth.py::test_rejects_expired_token_with_30s_leeway` | 過期 31 秒 → 401;過期 29 秒 → 通過 | ±30s 是**驗證方**的義務,IdP 端沒有開關(契約 §3.3) |
| T04 | `test_auth.py::test_jwks_supports_kid_rotation` | JWKS 有兩把 key 時依 `kid` 選對 | 金鑰輪替期新舊並存,假設只有一把會在輪替當天全掛 |
| T04 | `test_auth.py::test_server_side_refresh_before_access_token_expiry` | 模擬時間前進 300 秒後請求仍為已登入 | 🔴 契約 §3.3 陷阱:伺服器端算繪的 App 不會自己 refresh,症狀是「登入 5 分鐘後靜默變未登入,伺服器無任何錯誤」 |
| T04 | `test_auth.py::test_fake_idp_token_actually_expires` | 測試替身簽出的 token 必須真的會過期 | 契約明文提醒:**測試常用的假 token 永不過期,會把上面那個缺陷一起抹平** |
| T04 | `test_auth.py::test_fake_idp_token_carries_real_claims` | 假 token 必須含 `at_hash`、`sid`、`azp`、`nonce`;**缺 `at_hash` 時我方行為必須明確**(拒絕或忽略,二者擇一並斷言) | 🔴 契約 v3.2:PLM 開旗標當天**第一個真人登入 100% 失敗於 `at_hash`**,而其 **400 多支離線測試一支都沒抓到——測試自己造的 token 沒有那個 claim**(T03b 補) |
| T04 | `test_auth.py::test_redirect_uri_matches_registered_value` | 程式實際組出的 `redirect_uri` 必須**逐字等於** `https://catsapp.sporton.com.tw/inbox/oidc/callback/` | 🔴 契約 v2.14:PLM 因 Django `reverse()` 回**後註冊者**而首登即 mismatch,**錯誤停在 Keycloak 頁面、app 的 log 是空的**——查的人會先懷疑錯地方(T03b 補) |
| T04 | 手動(拿到 secret 後):壞 code 打 token 端點 | 回 `400 invalid_grant` = secret 正確;回 `invalid_client` = secret 錯 | 🔴 契約 v2.17:**用一個必定失敗的請求證明另一件事**,不需真人登入、不需 gateway 路由。正好治交付檔「取錯 64 字元值」那個坑(T03b 補) |
| T04 | `test_auth.py::test_login_route_issues_pkce_s256` | 導向 URL 必須帶 `code_challenge_method=S256`、一次性 `state`/`nonce`;**`code_verifier` 不得出現在 URL 裡**;scope 不含 `email` | 🔴 PKCE 退化成 `plain` 會**照樣登入成功**——沒有這條斷言,退化沒有任何症狀。scope 那半條釘住「刻意不申請 email」不只是口號(portal 已從 client 移除該 scope) |
| T04 | `test_auth.py::test_state_mismatch_is_rejected` | callback 的 `state` 與伺服器端存的不符 → **400 且不得建立 session** | CSRF 防護。只回 400 不夠——要斷言「沒有建 session」,否則擋了門卻已經放人進來 |
| T04 | `test_auth.py::test_refresh_failure_logs_user_out` | refresh 回 `invalid_grant`(帳號被停用)→ 立刻 **401**,不得沿用舊 session | 契約 §3.3 用 300 秒 access token 換「收權即時性」;失敗時沿用舊 session 會讓收權**變成假的**,而畫面上完全看不出來 |
| T04 | `test_auth.py::test_auth_routes_absent_without_issuer` | 未設 `INBOX_OIDC_ISSUER`/`INBOX_SESSION_SECRET` 時登入路由 **404**,健康檢查照常 200 | 回滾閥門(比照 portal 對 PLM 要求「旗標預設 off、off 時行為逐字不變」)。讓「部署了但 secret 還沒到」是明確狀態,不是使用者點下去才炸的 500 |
| T04 | `test_skeleton.py::test_env_example_oidc_values_match_portal_delivery_shape` | `.env.example` 的 `INBOX_OIDC_INTERNAL_BASE` **不得含 `/realms/`**;`INBOX_OIDC_ISSUER` 必須含且為 https | 🔴 實際踩到才補:兩者形狀不一致會組出 `/realms/sporton/realms/sporton` → discovery 404,而**它只在第一個真人登入時出現**,離線測試全綠 |
| T05 | `test_authz.py::test_first_login_creates_user_with_sub_only` | 新 sub 首登後 DB 只多一列;**schema 無 email/password/name 欄** | 契約 §4.2。schema 層斷言比行為層強——行為可以「現在剛好沒寫」,欄位不存在就是**寫不進去** |
| T05 | `test_authz.py::test_login_is_idempotent_no_duplicate_rows` | 同一 `sub` 重複登入不得長出第二列 | 「每次登入都 INSERT」在單人測試完全看不出來,要等第二次登入才出現,症狀是**同一個人有兩份角色** |
| T05 | `test_authz.py::test_first_login_grants_reader` | 首登即有 `reader` | DEC-16 經核可的偏離 |
| T05 | `test_authz.py::test_reader_cannot_send_message_403` | 🔴 **反向**:reader 寄信 → **403** | 核可條件 **C3**。**過寬的角色沒有症狀**——功能上一切正常,只在有人寄了不該寄的信時才顯現 |
| T05 | `test_authz.py::test_reader_cannot_publish_announcement_403` | 🔴 **反向**:reader 發公告 → **403** | 同上;401 是錯的答案(那代表憑證無效,呼叫方會查錯方向) |
| T05 | `test_authz.py::test_reader_can_be_disabled_per_user` | 停用後 403,**且下一次登入不得復活** | 核可條件 **C2**。「不得復活」是這條的一半——少了它,自動授與會把它加回來而畫面完全正常,整個停用是**表演** |
| T05 | `test_authz.py::test_auto_grant_can_be_globally_disabled` | 全域開關關掉 → 首登**零角色**、業務 API 403 | 核可條件 **C4**(portal 得單方撤回) |
| T05 | `test_authz.py::test_zero_role_user_gets_403_with_own_sub_shown` | 待開通頁 200 且**顯示本人 `sub`** | 契約 §4.3 的雞生蛋解法。少了這一半,第一個使用者會卡在一個**看不出下一步是什麼**的 403 |
| T05 | `test_authz.py::test_bootstrap_admin_applies_to_existing_pending_user` | 清單比對**每次登入**都做;冪等;**已停用者不得復活** | 🔴 upload-program 踩過:只在建號當下比對,對第一個管理員**永遠不會生效**(他早就登入過了) |
| T05 | `test_authz.py::test_display_name_never_read_in_authz_path` | **AST 源碼檢查**:`app/authz.py` 不得觸及 `display_name` | 契約 §4.2a L1 第 4 條。行為測試只證明「這條路徑現在沒讀」,源碼檢查證明「**沒有任何一條路徑讀得到**」 |
| T05 | `test_authz.py::test_display_name_only_written_from_own_login_token` | **AST 源碼檢查**:對 `display_name` 的賦值只能出現在 `app/repo.py` | 「僅得自本人登入 token」的可驗證性靠「寫入路徑只有一條」;多一條不會有錯誤訊息 |
| T05 | `test_authz.py::test_display_name_purge_tool_clears_bulk_and_single` | 單筆與整批都要**真的清得掉**,且回報清了幾列 | §4.2a L1 第 7 條。一個永遠成功卻什麼都沒清的工具**比沒有工具更糟**——它讓人以為已經清了 |
| T05 | `test_authz.py::test_admin_backend_requires_admin_role` | reader 進後台 403、admin 200;且後台**顯示快取的資料時間** | §4.2a L1 第 3 條(過期不隱藏而是標示) |
| T05 | `test_migration.py`(3 支) | up→down→up 在**真的 PostgreSQL** 上通過;down 後表**真的不見**;每個 version 的 `downgrade()` 不得是 `pass` | 🔴 SQLite 對 DDL 太寬鬆,在它上面綠的 migration 到 PG 可能**部署當下**才失敗。⚠ **本機只有 PG16**,**PG15 演練留 T11**;無 PG 時 **skip 而非 pass** |
| T06 | `test_auth.py::test_frontchannel_logout_is_idempotent_and_unauthenticated` | 免認證呼叫 → 204;重複呼叫 → 204;帶/不帶 `sid`+`iss` 皆 204 | 契約 §10.3;iframe 載入時不會帶 token |
| T06 | `test_auth.py::test_frontchannel_logout_only_deletes_cookie` | 端點不得建立 session、不得寫業務資料 | §10.3「被任意第三方呼叫的最壞後果必須是使用者被登出」 |
| T06 | `test_logout.py::test_frontchannel_route_is_registered_verbatim` | 打 client 登記值(無斜線)必須 **204**;307=我方 route 多了斜線、404=路徑不存在。帶斜線的變體預期 **307**(若也直接 204 表示兩個變體都註冊了),且 307 必須**保留 `sid`/`iss`** | 🔴 §10.3a:Keycloak 拿登記值去**呼叫**而非比對,「無斜線」不是規定、**逐字相同**才是。差一字元的症狀是**端點零呼叫**,與「設定沒套用」完全相同(PLM 2026-08-14 前例)。⚠ **本列於 2026-08-24 更正**,原寫「帶斜線變體必須 404(證明沒有靜默 redirect)」——見下方更正說明 |
| T06 | `test_logout.py::test_frontchannel_logout_with_sid_kills_that_session` | 拿 IdP 的 `sid`、**用一個完全沒有 cookie 的 client** 呼叫,原本那個 session 必須失效 | 🔴 front-channel 是 IdP 主動呼叫,iframe 的請求**可能沒有那個人的 cookie**(分頁凍結、cookie 政策、從別的脈絡發起)。只靠刪 cookie 的實作在那種情況下**靜默不生效**,使用者以為登出了而 inbox 還是登入狀態。這也是 T04 決定「session 放伺服器端」的唯一理由 |
| T06 | `test_logout.py::test_logout_clears_local_session_before_redirecting_to_idp` | 自發登出:302 到 IdP(帶 `id_token_hint`)、**且伺服器端 session 數減一**、**且重放抄走的 cookie 也是 401** | 🔴 順序不能反(先導 IdP 則使用者中途關分頁時本地 session 還活著)。**只斷言「登出後 /me 是 401」不夠**——刪掉瀏覽器 cookie 也會讓 /me 變 401,而伺服器端那個 session 可能還活著,抄到 cookie 的人就還能用。**這個洞是突變檢查抓出來的**(2026-08-24) |
| T06 | `test_logout.py::test_frontchannel_logout_response_is_not_cacheable` | 回應必須帶 `Cache-Control: no-store` | 被快取的登出等於登出無效,而症狀是**「有時登得出、有時登不出」**——間歇性症狀沒有人查得動 |
| T06 | `test_logout.py::test_logout_without_session_is_safe` | 未登入按登出 → 302 到 `/inbox/logged-out/`,不得 500、不得去打 IdP | 沒有 session 就沒有 `id_token_hint`,拿去 `end_session` 只會得到一個要使用者確認的頁面 |
| T06 | `test_logout.py::test_post_logout_redirect_uri_matches_registered_value` | 逐字等於 `https://catsapp.sporton.com.tw/inbox/logged-out/` | 🔴 與 T04 的 `redirect_uri` 同類,但症狀更難懂:Keycloak 對**未登記**的 post-logout 值會拒絕導回,使用者停在 IdP 頁面上,而我方 log 只看到「他登出了」——一切看起來正常 |
| T06 | `test_logout.py::test_logged_out_page_is_public_and_offline` | 免認證 200、含重新登入連結、**零外部資源**、無深色自動切換 | 契約 §4.10 禁外部 CDN。這一頁是按下登出後**唯一**會看到的東西,它 404 的話使用者體感是「登出把系統弄壞了」 |

### M3 通知核心(測試待寫)

| 任務 | 紅測試 | 斷言 | 為什麼 |
|---|---|---|---|
| T07 | `test_skeleton.py::test_migration_chain_reversible` | 每條 migration 有 backward;up→down→up 演練通過 | 共通紅線:動資料的 migration 必須可回滾 |
| T07 | `test_messages.py::test_schema_has_no_pii_columns` | 表結構無 email/姓名/密碼欄(`display_name` 除外且 nullable) | 契約 §4.2 |
| T07 | `test_schema.py::test_migration_0002_up_down_up` | 三張表出現/消失;**降到 `0001` 時 T05 的兩張表必須留著** | 🔴 後半是重點:`0002` 的 `downgrade()` 若手誤 drop 了 `app_user`,不看就不會發現——而那在正式環境是**把所有人的身分與角色刪掉** |
| T07 | `test_schema.py::test_message_schema_matches_upstream` | 上游 §4.1 欄位齊;**無** email/姓名/密碼欄;`body` 是 TEXT | VARCHAR(n) 超長在 PG 上是**報錯**,症狀是「某些通知推不進來」 |
| T07 | `test_schema.py::test_message_recipient_sub_has_no_fk` | 🔴 斷言外鍵**不存在** | 推送 API 必須能推給**從未登入過的人**;設了外鍵那些推送被資料庫擋掉,而**收件人不會知道有東西被丟掉**。這條擋的是「日後有人順手把外鍵補上」——那個動作看起來在修缺漏,實際是把能力靜默關掉 |
| T07 | `test_schema.py::test_announcement_read_has_fks_and_cascades` | 兩個外鍵都在且 CASCADE | ⚠ 與上一條**相反**是刻意的:標公告已讀者必然已登入過。理由寫在兩邊註釋裡,否則日後看起來像不一致 |
| T07 | `test_schema.py::test_announcement_read_unique_per_person` | `(announcement_id, user_sub)` 唯一 | 上游 §4.2「公告不逐人複製」。沒有約束時重複標已讀會長出重複列,而**已讀在畫面上仍然正常**,只有列數在悄悄長 |
| T07 | `test_schema.py::test_unread_count_index_exists` | `(recipient_sub, is_read)` 有索引 | A.1 明訂未讀鈴鐺 **30 秒輪詢**;無索引時是全表掃描,而**資料少時完全看不出來**,症狀是「整個入口變慢」 |
| T07 | `test_schema.py::test_category_check_rejects_unknown_value` | 真的 INSERT 一筆壞值看它被擋 | 讀約束定義只證明「約束存在」,INSERT 才證明「它會擋」 |
| T07 | `test_schema.py::test_audience_check_allows_only_all_for_now` | `all` 可、`group:*` 被擋 | 🔴 上游支援群組定向而**我方做不到**(需 `groups` claim,client 刻意未申請)。**建了欄位卻假裝能用**比不建更糟:發布端會以為設了收件範圍,而實際上每則都送給所有人 |
| T07 | 🔴 `test_schema.py::test_models_match_migration_schema` | 同一個 PG 上開兩個庫:一邊 migration、一邊 `create_all()`,逐表逐欄比對(表名/欄位名/nullable) | 🔴 **補 T05 誠實標註的盲點**:應用層測試的 schema 來自 `create_all()` 而正式環境來自 migration,**在此之前沒有任何一支測試在比較兩者**;漂移的症狀是「本機全綠、部署後少一個欄位」。⚠ 刻意不比型別字串(等價但不同名的表示會產生假紅燈)|
| T08 | `test_messages.py::test_cannot_read_other_users_messages` | 讀 `recipient_sub != 自己` → **403** | deny-by-default 的核心;這是本系統最重要的一條 |
| T08 | `test_messages.py::test_sender_sub_ignores_client_input` | 前端傳 `sender_sub` 一律被忽略,以 token 的 sub 為準 | 寄件人不可偽造(A.3) |
| T08 | `test_messages.py::test_mark_read_is_idempotent` | 重複標已讀 → 同樣 200,`read_at` 不變 | 輪詢客戶端會重送 |
| T09 | `test_announcements.py::test_reader_cannot_publish_and_writes_nothing` | 無 `announcer` 發公告 → **403 且資料庫零新增列** | 公告是廣播,權限錯的半徑最大。🔴 **只驗狀態碼不夠**:「先寫進去、再回 403」的實作會照樣綠,而公告已經在表上了 |
| T09 | `test_announcements.py::test_expired_announcement_not_in_active` | `ends_at` 已過的公告不得出現在 active | |
| T09 | 🔴 `test_announcements.py::test_future_announcement_not_in_active` | `starts_at` 未到的公告**也**不得出現 | **最容易漏的那一半**:只驗 `ends_at` 的實作會讓排程下週的公告當場就出現,而發布者以為排程生效了 |
| T09 | `test_announcements.py::test_window_boundary_start_inclusive_end_exclusive` | 邊界語意 `starts_at <= now < ends_at`,以**注入的 now** 驗 | 用真實時鐘驗邊界會隨機失敗,而失敗看起來像程式錯 |
| T09 | 🔴 `test_announcements.py::test_read_by_one_person_does_not_mark_it_read_for_others` | 甲標已讀後,乙的那則仍是未讀 | 擋「把已讀寫在公告本身」:一個人讀完全公司都變已讀,而**其他人只是覺得自己好像看過** |
| T09 | `test_announcements.py::test_mark_read_is_idempotent_and_one_row_per_person` | 重複標:不新增列、`read_at` 不變 | 複製列時**畫面完全正常**,只有列數在悄悄長 |
| T09 | 🔴 `test_announcements.py::test_naive_datetime_is_rejected_400` | 不帶時區的時間 → **400** | 台灣的 `02:00` 當 UTC 存差 8 小時,公告晚 8 小時出現而零錯誤(portal 憲法第四條7 的同一種坑)|
| T09 | `test_announcements.py::test_inverted_window_is_rejected_400` | `ends_at <= starts_at` → **400** | 空窗公告=發布成功但永遠不出現,而回應是 201 |
| T09 | `test_announcements.py::test_active_response_has_no_author_identity` | 回應無 `author_sub`、無姓名(**先寫入 L1 快取再斷言**)| §4.2a L1;不先寫入的話快取為空時必然通過=**假綠**(T08 踩過)|
| T09 | 🔴 `test_announcements.py::test_inbox_page_shows_active_announcements` | 收件匣頁顯示有效公告、不顯示過期的 | 沒有這條的話**公告上線後沒有任何畫面顯示它**,而每一層都回 200 |
| T09 | `test_announcements.py::test_every_timestamp_in_api_responses_carries_a_timezone` | 公告**與訊息**的每個 `*_at` 都帶時區位移 | 我方拒收不帶時區的**輸入**,就不能吐出不帶時區的**輸出**。⚠ 在 SQLite 上 `.isoformat()` 是 naive、PG 上帶時區——**本機與正式行為不同** |
| T09b | 🔴 `test_publish_form.py::test_post_without_csrf_is_403_and_writes_nothing` | 缺 CSRF token → **403 且零列寫入** | 本專案第一個 POST 表單。CSRF 成功時**沒有錯誤訊息** —— 受害者只會看到一則自己沒發過的公告 |
| T09b | 🔴 `test_publish_form.py::test_csrf_token_of_another_session_is_rejected` | 別的 session 的 token 不得通行 | 擋「token 只驗簽章、不綁 session」:攻擊者在自己 session 拿一個合法 token 就能打受害者,而它通過所有「有 token 就放行」的測試 |
| T09b | 🔴 `test_publish_form.py::test_csrf_token_is_not_the_session_key_in_disguise` | token 不得含 session key;且必須是**純** 64 字元 HMAC 十六進位 | 用簽章(非 HMAC)做 token 會把 session key **明文帶到 HTML 上**。⚠ 第一版斷言拿 `cookie.split(".")[0]` 當 key,而**那是 key 的 base64** —— 突變逃掉了 |
| T09b | 🔴 `test_publish_form.py::test_form_datetime_is_interpreted_as_taipei_time` | 表單填 `2026-08-30T02:00`(台北)→ DB 存 `2026-08-29T18:00Z`,**斷言實際的 UTC 值** | 只斷言「發布成功」的話,差 8 小時也會回 303。⚠ 比對時**不可用 `astimezone`**(對 naive 會當成執行機器的本地時區,CI 上剛好對而開發機差 8 小時)|
| T09b | 🔴 `test_publish_form.py::test_bad_input_rerenders_form_with_readable_message` | 五種 400:**在錯誤框內**有具體訊息、**已填內容仍在**、零列寫入 | ⚠ 兩個第一版的洞:測試資料用「標題」「內容」而那兩個詞在 `<label>` 裡本來就有;對**整頁** `re.search` 欄位名 —— 兩者都讓斷言永遠成立 |
| T09b | `test_publish_form.py::test_inbox_shows_publish_link_only_to_announcer` | `announcer` 看得到、`reader` 看不到(**兩個方向都測**) | 只測一個方向的話,「永遠顯示」或「永遠不顯示」各有一半會通過 |
| T09b | `test_publish_form.py::test_published_announcement_appears_on_inbox_immediately` | 成功 **303** 導回 `/inbox/`,公告當場出現 | 回 200 的話,使用者按重新整理會**再發一則** |
| T09b | `test_publish_form.py::test_form_page_is_csp_clean` | CSP + `<style>` 帶本次 nonce + **零 JS** + 零行內樣式屬性;**且先斷言 200 與「找得到 `<style>`」** | ⚠ 少了後半,這支測試在「路由還不存在」時**會通過**:404 的 JSON 沒有那些東西,而 CSP 標頭是 middleware 加的 |
| T10 | `test_security.py::test_message_subject_and_body_are_escaped_on_page` | 送入 `<script>alert(1)</script>`,輸出是跳脫後的字面文字。⚠ **同時斷言跳脫後的字面「有出現」** | 🔴 R1:同源之下一次 stored XSS 可觸及 IdP。只驗「原始標籤不出現」的話,「乾脆不顯示 body」也會通過 |
| T10 | `test_security.py::test_announcement_title_and_body_are_escaped_on_page` | 公告同樣跳脫 | 公告一則對多人,注入的影響半徑比單封訊息大 |
| T10 | `test_security.py::test_admin_page_escapes_display_name` | 後台的顯示名稱也跳脫 | 那個值來自 IdP,不是我方產生的 |
| T10 | 🔴 `test_security.py::test_action_url_rejects_counterexamples` | **反例 14 則**一律 400:外部網址、`http` 降級、`javascript:`(含大小寫變形)、`data:`、`//`、**`/\`**、**`SITE + ".evil.tld"`**、**`SITE + "@evil.tld"`**、無 scheme、NUL、換行、超長 | 🔴 R2:站內通知是現成的釣魚載具。三個經典洞:**`/\` 也是協定相對**(瀏覽器正規化成 `//`)、**前綴冒充**(通過 `startswith` 而網域是別人的)、**userinfo 冒充** |
| T10 | `test_security.py::test_action_url_accepts_same_site` | 正例 6 則必須通過(含 scheme/host 大小寫變形) | ⚠ 白名單太嚴會讓真的通知發不出去 —— 那是 400(看得見),但仍是 bug |
| T10 | 🔴 `test_security.py::test_message_is_only_constructed_in_repo_create_message` | **AST 源碼檢查**:`app/` 底下只有 `repo.create_message()` 構造 `Message` | 行為測試只證明「這條路徑現在有驗」;源碼檢查證明「**沒有第二條路徑**」。`action_url` 的端點是 T14 才有的 |
| T10 | 🔴 `test_security.py::test_bad_action_url_already_in_db_is_not_rendered` | 直接塞一列 `javascript:` 進 DB(繞過應用層)→ 頁面與 API 都不得輸出 | 寫入側白名單是 T10 才加的,**在它之前寫進去的列全都繞過了它** |
| T10 | `test_security.py::test_good_action_url_still_renders` | 合法的 `action_url` 必須還在 | 沒有這條的話,「永遠回 None」的實作會讓上一條變綠,而按鈕從此不出現 |
| T10 | `test_security.py::test_subject_and_body_never_appear_in_logs` | 全流程抓 stdout,不得含主旨/內容/公告標題;且斷言**有抓到 log** | A.3:log 只記 id、sub、事件類型。不斷言「有抓到」的話會變成永遠通過的空檢查 |
| T10 | `test_security.py::test_every_log_line_is_single_line_json` | 每一行都可 `json.loads` 且有 `event` 欄位 | 共通紅線:log 走 stdout、單行 JSON |
| T10 | 🔴 `test_security.py::test_every_html_response_has_csp` | **列舉所有 GET 路由**,凡回 `text/html` 者一律要有 CSP | 逐路由加標頭的話下一個新頁面會忘記,**而忘記沒有症狀**;列舉式守門讓新頁面自動被涵蓋 |
| T10 | 🔴 `test_security.py::test_inline_style_carries_the_nonce_from_this_response` | 從**回應標頭**取出 nonce,斷言頁內每個 `<style`/`<script` 帶**那一個**值 | 對不上的症狀是「整頁沒樣式而伺服器零錯誤」。只驗「標頭有 nonce」或「模板有 nonce 屬性」都不夠 |
| T10 | `test_security.py::test_nonce_differs_between_requests` | 兩次請求的 CSP 不同 | 寫死一個 nonce **等於完全沒有 nonce**,而畫面上一模一樣 |
| T10 | 🔴 `test_security.py::test_rendered_pages_have_no_inline_style_attributes` | 算繪後的頁面不得有行內樣式屬性 | 🔴 **T10 才發現**:nonce 只對**元素**有效,`style-src 'nonce-…'` 會把 `style="…"` 屬性整批擋掉 —— 「`<style>` 帶了 nonce」**不等於**「樣式會生效」。⚠ 驗算繪後而非模板原始碼(註解裡會提到這個屬性名) |
| T10 | `test_security.py::test_local_stylesheet_is_still_allowed_and_served` | `style-src` 含 `'self'` 且 Bootstrap 載得到 | 沒有這條的話,「CSP 嚴到把自家 CSS 也擋掉」會讓每支 CSP 測試都綠而整頁沒樣式 |

### M4–M6(測試待寫)

| 任務 | 紅測試 | 斷言 |
|---|---|---|
| T11 | 手動:`curl -skI https://catsapp.sporton.com.tw/inbox/health` | 200;且既有路由(`/plm/` `/TMP_GEN/` `/core/`)zero-diff |
| T12 | SSO 契約 §7 冒煙清單 | 🔴 SLO 項測試前**必須清 cookie 或用無痕視窗**——否則舊 cookie 直接放行、沒走 OIDC 交換,IdP 手上沒有 client session 可通知,**端點零呼叫的症狀與「設定沒套用」完全相同**(§10.7) |
| T14 | `test_push.py::test_push_requires_s2s_token` | 無 token / 使用者 JWT / 錯 `aud` → 一律 **401** |
| T14 | `test_push.py::test_push_verifies_scope_per_endpoint` | token 的 `aud` 正確但**缺 `notification:push` scope** → **403**(**不是 401**)。🔴 §11.5:401=憑證無效、403=憑證有效但無此權限,**混用會讓呼叫方查不出是憑證錯還是授權不足**;§11.9 第 2 坑:只驗 `aud` 的話兩條流的 token 互打得動,**而兩邊的測試都會過**(T03b 更正 401→403) |
| T14 | `test_push.py::test_unregistered_azp_is_denied` | 未登記的 `azp`(即使 `aud`/scope 都對)→ **403**;**不得因「來源是內網」放行** | 🔴 §11.5 第 3 條逐字:「**內網不是身分**」。`source_app` 由 `azp` 查 DB 表推導,查不到就是拒收(T03b 補) |
| T14 | `test_push.py::test_x_user_id_is_verified_not_just_logged` | 缺 `X-User-Id` → **400**;非 UUID 形狀 → 400;等於呼叫方 service account 自身 → 400。🔴 不得「缺就當系統發出」——那會讓稽核鏈在最需要的時候恰好是空的(portal 加嚴條件) |
| T14 | `test_push.py::test_idempotency_key_dedupes` | 同一 `Idempotency-Key` 重送 → 200 但**訊息數不變**;缺 key → 400 |
| T14 | `test_push.py::test_source_app_ignores_body` | body 帶 `source_app: "portal"` 而呼叫方是別的 client → 以 **client 身分**為準。🔴 能自稱來源=能冒充任何系統發通知,而通知帶著平台的官方外觀 |
| T16 | `test_messages.py::test_direct_message_requires_sender_role` | 無 `sender` 角色寄信 → 403 |
| T16 | `test_messages.py::test_recipient_must_have_logged_in_before` | 收件人不在「已首登使用者」名冊 → 400(對齊 D7-7「首登才建列」) |

---

## 4. 測試資料原則

1. **一律 fixture 現造假資料**;嚴禁真實個資、正式資料匯出檔進測試與 CI(共通紅線)。
2. 假 sub 一律用固定的假 UUID(如 `00000000-0000-4000-8000-00000000000x`),不取自任何真實帳號。
3. 🔴 **假 IdP 的 token 必須真的會過期、簽章必須真的可被驗**——契約 §3.3 明文提醒:抹平過期行為的測試替身會把「登入 5 分鐘後靜默登出」這類缺陷一起藏起來。
4. 🔴 **假 token 必須帶真實 IdP 會給的 claims,尤其 `at_hash`**(2026-08-18 新增,來源:契約 v3.2 記載的 PLM 實例)。
   > PLM 開旗標當天,**第一個真人登入 100% 失敗於 `at_hash`**,而它的 **400 多支離線測試一支都沒抓到**
   > ——**因為測試自己造的 token 沒有那個 claim**。真實 IdP 多給一個 claim 就改變了函式庫的行為。
   > 那次是「§7 冒煙第一次真的擋下東西」。
   我方的對應動作:`test_auth.py` 的假 IdP 必須簽出**與 Keycloak 實際回傳同一組 claims**
   (含 `at_hash`、`sid`、`azp`、`nonce`),並新增一支**反向測試**:少了 `at_hash` 時我方的驗證行為必須是明確的
   (要嘛拒絕、要嘛忽略),**不得因函式庫預設而在真實環境才第一次顯現**。
   ⚠ 這是**同型盲點**:上一條(過期)與本條(claims 完整性)都是「測試替身比真實 IdP 寬鬆」。
5. 安全類測試**必須含反例**(壞輸入被拒),只測好路徑等於沒測。

## 5. CI 與覆蓋現況(誠實標示)

| 項目 | 狀態 |
|---|---|
| `tests/run_all.sh` 於 push/PR 執行 | ✅ `.github/workflows/ci.yml` |
| 文件層 40 項 | ✅ 已跑、全綠 |
| 骨架層 20 項 | ✅ 已跑、全綠 |
| 應用層 4 項 | ✅ 已跑、全綠 |
| **auth 層 12 項(T04)** | ✅ **已跑、全綠**(2026-08-21) |
| **登出層 9 項(T06)** | ✅ **已跑、全綠**(2026-08-24) |
| **授權層 13 項(T05)** | ✅ **已跑、全綠**(2026-08-24);含 2 支 AST 源碼檢查 |
| **收件匣層 14 項(T08)** | ✅ 已跑、全綠(2026-08-25);含靜態資源守門 |
| **公告層 21 項(T09)** | ✅ 已跑、全綠(2026-08-25);含邊界注入時鐘與「輸出時間必帶時區」|
| **安全層 41 項(T10)** | ✅ 已跑、全綠(2026-08-25);含 20 個 `action_url` 參數化案例、1 支 AST 源碼檢查、CSP 的路由列舉守門 |
| **發布表單層 17 項(T09b)** | ✅ 已跑、全綠(2026-08-25);含 CSRF 三態、時區的實際 UTC 值斷言 |
| **schema 層 10 項(T07)** | ✅ 已在**真的 PostgreSQL 16.13** 上跑過;含 model↔migration 的 schema 比對 |
| **migration 層 3 項(T05)** | ✅ 已在**真的 PostgreSQL 16.13** 上跑過。🔴 **PG15 未演練**(本機無 PG15、無 docker daemon)——留 T11 於 VM 補;⚠ 無 PG 時本組 **skip**,`run_all.sh` 會把 skip 數量印出來 |
| M4–M6 測試 | ⬜ **尚未撰寫**(規格見 §3;不得視為已覆蓋)。⚠ T14 的規格已於 2026-08-18 由 portal 核定,五支測試的斷言已具體化 |
| T10b / T11 / T12 / T14–T17 測試 | ⬜ 尚未撰寫(規格見 §3)|
| 真 IdP / gateway 冒煙 | ⬜ **尚未執行**(client 已核發,但 secret 尚未進本服務;需 T11 路由。CI 跑不動) |
| `docker compose config` 解析 | ✅ 已於本機驗過(Compose v5.1.1);**容器實際啟動尚未驗**(本環境無 docker daemon) |

### 5.1 突變檢查(2026-08-21 首次執行)

「測試全綠」只證明**現在**是對的,不證明**測試會抓到退化**。T04 完工前把實作
逐項改壞一行,看對應測試是否轉紅:

| 故意改壞 | 對應測試 | 結果 |
|---|---|---|
| `LEEWAY_SECONDS` 30 → 0 | `test_rejects_expired_token_with_30s_leeway` | ✅ 紅 |
| `code_challenge_method` S256 → plain | `test_login_route_issues_pkce_s256` | ✅ 紅 |
| `redirect_uri` 去掉結尾斜線 | `test_redirect_uri_matches_registered_value` | ✅ 紅 |
| `at_hash` 不比對 | `test_fake_idp_token_carries_real_claims` | ✅ 紅 |
| 移除主動續期 | `test_server_side_refresh_before_access_token_expiry` | ✅ 紅 |
| refresh 失敗仍放行 | `test_refresh_failure_logs_user_out` | ✅ 紅 |
| JWKS 遇未知 kid 不重抓 | `test_jwks_supports_kid_rotation` | ✅ 紅 |
| `state` 不比對 | `test_state_mismatch_is_rejected` | ✅ 紅 |
| alg 三層防護全拆 | `test_rejects_hs256_and_none_alg` | ⚠ **仍綠**(見下) |

🔴 **最後一項是這次最有價值的發現,而它有兩層:**

① **第一次跑時它「假綠」**:替身簽的 HS256 假 token **沒有 `kid`**,所以拆掉
   alg 閘門後,token 是在「找不到 kid」那一關被擋下的——**測試綠的理由不是它
   宣稱的那個**。已修:簽錯演算法的假 token 一律帶真實存在的 `kid`
   (演算法混淆攻擊的標準做法就是從真 token 抄走 kid,只換 alg)。

② 修好之後它**還是綠**,而這次是對的:HS256 被拒有**三層**保證——我方的
   `alg != "RS256"` 閘門、PyJWT 的 `algorithms=["RS256"]` 白名單、以及 PyJWT
   拒絕用非對稱金鑰做 HMAC。任拆一層行為都不變。⚠ **但第三層不是我方的**:
   換掉 JWT 函式庫那層就沒了,所以顯式閘門必須留著。

💡 突變檢查用的腳本刻意**不進 repo**:它會改寫 `app/` 的原始碼,放進 CI 是
   把「改壞正式程式碼」變成一個常設能力。列為後續建議(§5 遺留)。


### 5.2 一處規格更正(2026-08-24,T06 動工時)

**原規格(v1.2 寫的):** T06 的帶斜線變體「必須 **404**(證明沒有靜默 redirect)」。

**實測:** FastAPI 對 `/inbox/oidc/frontchannel-logout/` 回 **307** 導到無斜線的 route,
**且保留 `sid` 與 `iss` 查詢字串**;而 iframe 會跟隨 307。

🔴 **重判後認定原規格的理由是錯的。** 它把那個 redirect 當成「會遮蔽登記不一致」的
危害,但在正式環境它其實是**安全網**——portal 萬一登記成有斜線的變體,307 讓它
**照樣生效**,而不是變成「端點零呼叫」。把它改成 404 在正式環境買不到任何東西,
只讓一個登記錯字變成致命。

**改法:** 斷言改為「打登記值(無斜線)必須 **204**」——307 就代表我方 route 帶了
斜線,404 代表路徑不存在,兩種漂移都抓得到,而且**不依賴 redirect 行為**。

⚠ **同時放棄了另一個做法:列舉 `app.routes` 斷言路徑字串。** 本版 FastAPI(0.141)
把 `include_router` 的結果包成一個 `_IncludedRouter` 物件,列舉頂層**只拿到自動產生
的 `/docs` 與 `/openapi.json`,一個實際端點都拿不到**。靠內部結構寫的斷言會在升版時
**悄悄改變它在檢查什麼**——行為斷言不會。

### 5.3 突變檢查(T06,2026-08-24)

| 故意改壞 | 對應測試 | 結果 |
|---|---|---|
| front-channel route 加結尾斜線 | `test_frontchannel_route_is_registered_verbatim` | ✅ 紅 |
| front-channel 不依 `sid` 刪 | `test_frontchannel_logout_with_sid_kills_that_session` | ✅ 紅 |
| 自發登出改成「先導 IdP 再清」 | `test_logout_clears_local_session_before_redirecting_to_idp` | ✅ 紅(**補洞後**) |
| post-logout 改用未登記的 `/inbox/` | `test_post_logout_redirect_uri_matches_registered_value` | ✅ 紅 |
| 拿掉 `Cache-Control: no-store` | `test_frontchannel_logout_response_is_not_cacheable` | ✅ 紅 |
| front-channel 順手建 session | `test_frontchannel_logout_only_deletes_cookie` | ✅ 紅 |

🔴 **第三列是這次的收穫:它第一次跑是「未被抓到」。** 原測試只斷言「登出後
`/inbox/me` 回 401」,而**刪掉瀏覽器的 cookie 也會讓 /me 變 401**——伺服器端那個
session 其實還活著,抄到 cookie 值的人仍然是登入狀態。已補兩條斷言:
伺服器端 session 數必須減一、且**重放抄走的 cookie 必須 401**。


### 5.4 突變檢查(T05,2026-08-24)

| 故意改壞 | 對應測試 | 結果 |
|---|---|---|
| `reader` 多拿 `send_message`(C1 範圍放寬) | `test_reader_cannot_send_message_403` | ✅ 紅 |
| 無權時回 401 而非 403 | `test_reader_cannot_publish_announcement_403` | ✅ 紅 |
| `grant_role` 復活已停用的角色 | `test_reader_can_be_disabled_per_user` | ✅ 紅 |
| bootstrap 清單只在建號當下比對 | `test_bootstrap_admin_applies_to_existing_pending_user` | ✅ 紅 |
| 全域開關失效(撤回不生效) | `test_auto_grant_can_be_globally_disabled` | ✅ 紅 |
| 每次登入都 INSERT 新列 | `test_login_is_idempotent_no_duplicate_rows` | ✅ 紅 |
| purge 什麼都不清但回報成功 | `test_display_name_purge_tool_clears_bulk_and_single` | ✅ 紅 |
| 授權判定改讀 `display_name` | `test_display_name_never_read_in_authz_path` | ✅ 紅 |
| `downgrade()` 改成 `pass` | `test_migration.py::test_downgrade_is_not_a_stub` | ✅ 紅 |
| 後台不驗 `admin` 角色 | `test_admin_backend_requires_admin_role` | ✅ 紅 |

**10 項全被抓到。**

### 5.5 T05 期間找到的兩個測試自身的缺陷

1. 🔴 **相對路徑讓同一支測試在兩個入口點結果不同。** 兩支 AST 檢查原本寫
   `pathlib.Path("app/authz.py")`,而 `run_all.sh` 是 `cd tests/` 之後才跑 pytest
   ——從 repo 根跑 **61 綠**、經 `run_all.sh` 跑 **2 紅**,而紅的訊息看起來像
   程式壞了。已一律改用 conftest 的 `ROOT` 組路徑。
2. `test_downgrade_is_not_a_stub` 把**所有** `ast.Expr` 都當 docstring 濾掉,
   而 `op.drop_table(...)` 本身就是一個 expression statement ——
   於是每一個**正確的** downgrade 都會被判成空的。已改為只去掉開頭的 docstring。
   ⚠ 它第一次跑就紅,而**紅的原因在測試自己身上**;真正的 up→down→up 演練
   同時獨立通過,兩個角度裡不夠力的那個剛好是它。


### 5.6 突變檢查(T07,2026-08-24)

| 故意改壞 | 對應測試 | 結果 |
|---|---|---|
| 順手給 `recipient_sub` 補上外鍵 | `test_message_recipient_sub_has_no_fk` | ✅ 紅 |
| 拿掉未讀鈴鐺的索引 | `test_unread_count_index_exists` | ✅ 紅 |
| 拿掉公告已讀的唯一約束 | `test_announcement_read_unique_per_person` | ✅ 紅 |
| 外鍵不設 CASCADE | `test_announcement_read_has_fks_and_cascades` | ✅ 紅 |
| `category` 的 CHECK 放寬 | `test_category_check_rejects_unknown_value` | ✅ 紅 |
| `audience` 放寬到群組 | `test_audience_check_allows_only_all_for_now` | ✅ 紅 |
| `downgrade` 順手刪掉 `app_user` | `test_migration_0002_up_down_up` | ✅ 紅 |
| `body` 改成 `VARCHAR(255)` | `test_message_schema_matches_upstream` | ✅ 紅 |
| migration 漏一個欄位(model 有、migration 沒有) | `test_models_match_migration_schema` | ✅ 紅 |

**9 項全被抓到。** 其中最後兩項是 T07 新增的防線:
「migration 漏一個欄位」在此之前**沒有任何東西會發現**。


### 5.7 突變檢查(T08,2026-08-25)

**9 項全被抓到。** 兩項值得單獨記:

| 故意改壞 | 對應測試 | 結果 |
|---|---|---|
| 列表少了 `recipient_sub` 的 `WHERE` | `test_list_returns_only_own_messages` | ✅ 紅 |
| 標已讀只比 id 不比收件人 | `test_mark_read_on_others_message_is_403` | ✅ 紅 |
| 標已讀每次覆寫 `read_at` | `test_mark_read_is_idempotent_and_preserves_read_at` | ✅ 紅 |
| 資源移出 mount 目錄 | `test_every_template_asset_is_actually_served` | ✅ 紅 |
| **回應帶上姓名欄位** | `test_inbox_page_has_no_personal_names` | ⚠ **第一次未被抓到** → 補斷言後 ✅ 紅 |

🔴 **「回應帶上姓名欄位」第一次逃掉,而那是測試真的有洞。**
原測試只驗 **HTML 頁面**上沒有姓名 —— 而往 `_serialize` 加一個
`sender_name` 欄位**測試照樣綠**,因為模板沒有算繪它。
但**前端 JS 拿得到**,而契約 §4.2a L1 禁的是「出現在一般使用者可見的面」,
JSON 欄位就是那個面。已補兩條:①API 回應不得出現快取的姓名字串;
②**結構性**斷言——回應項目不得有任何含 `name` 的鍵(擋「加了欄位但值剛好
不是這次快取的那個字串」,而那同樣是洩漏)。

⚠ **另記一件量測工具的問題(第二次)。** 突變腳本的 write→test→restore
在同一秒內完成時,還原後檔案的 `(mtime, size)` 可能與 `__pycache__` 裡
`.pyc` 記錄的相符,於是 Python **沿用由突變後原始碼編出的 bytecode** ——
突變因此**留在工作目錄裡**,而 `git status` 看不出來(原始碼是對的)。
症狀是「原始碼看起來對,而行為是錯的」,查的人會先懷疑程式。
已在腳本每次還原後清 `__pycache__`。
🔴 這是**不把突變腳本放進 repo** 那個決定(T04)的第二個具體理由:
它會改寫工作目錄,而還原不是原子的。



### 5.8 突變檢查(T09,2026-08-25)

**17 項全被抓到。** 四項值得單獨記:

| 故意改壞 | 對應測試 | 結果 |
|---|---|---|
| 有效期不驗 `starts_at`(排程公告當場出現) | `test_future_announcement_not_in_active` | ✅ 紅 |
| `user_sub` 從 JOIN 的 ON 搬到 WHERE | `test_read_by_one_person_does_not_mark_it_read_for_others` | ✅ 紅 |
| 發布端點改成只要 `read_own` | `test_reader_cannot_publish_and_writes_nothing` | ✅ 紅 |
| 輸出時間退回 `.isoformat()` | `test_every_timestamp_in_api_responses_carries_a_timezone` | ✅ 紅 |

⚠ **第一輪有 1 項顯示「未抓到」,查為工具錯,不是測試有洞。**
那個 `sed` 把 `Announcement.starts_at <= at,` 改成 `Announcement.starts_at <= at, True,`
—— 條件還在,只是多了一個恆真項,**根本不是我想測的那個突變**。
改成整條替換為 `True,` 之後立刻紅。
🔴 這與 T06 的「拿掉 `no-store`」是同一種誤判:**突變沒套用**與
**測試沒抓到**在腳本輸出上長得一模一樣。故本次腳本加了
「sed 前後檔案沒變就報『突變沒套用』」這一道 —— 它擋的正是這個。

🔴 **第二欄的 `user_sub` 那一項值得記住**:把它從 ON 搬到 WHERE,
結果不是「已讀狀態錯了」,是**別人一讀,那則公告就從我的清單裡消失**
—— 而消失的東西沒有人會回報。


### 5.9 突變檢查(T10,2026-08-25)

**19 項全被抓到** —— ⚠ **這是修好量測工具之後的數字**(見下)。四項值得記:

| 故意改壞 | 對應測試 | 結果 |
|---|---|---|
| 前綴比對不要求下一字元是 `/` | `test_action_url_rejects_counterexamples` | ✅ 紅 |
| 輸出側直接吐原值(舊資料的 `javascript:` 就出去了) | `test_bad_action_url_already_in_db_is_not_rendered` | ✅ 紅 |
| 輸出側全部丟掉(守門過頭) | `test_good_action_url_still_renders` | ✅ 紅 |
| 把行內樣式屬性加回來 | `test_rendered_pages_have_no_inline_style_attributes` | ✅ 紅 |

🔴 **量測工具第三次出問題,而這次的方向相反:它會產生「假 ✅」。**

腳本把 `pytest` 的**非零離開碼**一律讀成「測試紅了 = 抓到」。而 `-k` 選擇器打錯時:

| 情況 | pytest 離開碼 | 腳本原本的判讀 |
|---|---|---|
| 測試真的紅了 | 1 | ✅ 抓到 |
| **全部被 deselect**(`-k` 打錯) | **5** | ✅ 抓到 ← **假的** |
| **指定不存在的測試** | **4** | ✅ 抓到 ← **假的** |

本次第一輪就有兩個選擇器打錯(`-k protocol_relative`、`-k csp` 都選不到任何測試)。

🔴 **這比前兩次嚴重。** T06 的「sed 沒配到」與 T09 的「sed 改錯語意」都讓結果偏向
**假 ❌**(保守,會被人追查);這一次偏向**假 ✅**(樂觀,沒有人會追查)。

已修:腳本現在分辨 `rc=0` / `rc=4,5` / 其他三種,並在 4/5 時明講
「選擇器選不到任何測試」。

⚠ 三次的共同形狀:**量測工具本身沒有測試**。所以每一輪都要看它印的**警示行**,
不是只看最後那個總數。


### 5.10 突變檢查(T09b,2026-08-25)

**19 項全被抓到** —— 🔴 **這是修掉三個測試洞之後的數字。**

第一輪有 **3 項沒抓到,而三個都是我的斷言寫錯**(不是量測工具問題):

| 突變 | 為什麼逃掉 | 修法 |
|---|---|---|
| CSRF token 把 session key 直接串進去 | 拿 `cookie.split(".")[0]` 當 session key 比對,而**那是 key 的 base64** | 改用 `store.unseal(cookie)`;加一條「token 必須是**純** 64 字元 HMAC 十六進位」 |
| 400 時清空表單 | 測試值用「標題」「內容」,而那兩個詞**在 `<label>` 裡本來就有** | 值改成不會出現在模板裡的字串 |
| 400 時只寫「發布失敗」 | 對**整頁** `re.search(欄位名)`,那些詞在 label 裡本來就有 | 先抓出 `alert-danger` 那一塊,只在**錯誤框內**比對 |

🔴 **共同形狀:斷言的對象裡本來就含有我要找的字串。**
三者都讓測試看起來很嚴格而實際上**永遠通過** —— 而只跑一次是綠的,
**不會有任何線索**。

⚠ 這是突變檢查第一次抓到**測試本身**的邏輯錯誤;
§5.7 / §5.8 / §5.9 那三次抓到的都是**量測工具**的問題
(sed 沒配到、sed 改錯語意、選擇器選不到測試)。兩類都要防。

---

## 版本歷史

| 版本 | 日期 | 修改人 | 摘要 |
|---|---|---|---|
| v1.10 | 2026-08-25 | Benny | **T09b 完工回寫**:§3 新增 T09b 的 **8 列**(CSRF 三態、時區的實際 UTC 值、五種 400 的「錯誤框內 + 保留輸入」、入口連結雙向、303、CSP 乾淨);§5 覆蓋現況加**發布表單層 17 項**;新增 §5.10 突變檢查 **19/19**。🔴 §5.10 記下**三個測試洞**,共同形狀是**斷言的對象裡本來就含有我要找的字串**——三者都讓測試看起來嚴格而**永遠通過**,而只跑一次是綠的不會有任何線索。⚠ 這是突變檢查第一次抓到**測試本身**的邏輯錯誤;前三次(§5.7–5.9)抓到的都是**量測工具**的問題。兩類都要防 |
| v1.9 | 2026-08-25 | Benny | **T10 完工回寫**:§3 的 T10 由 3 列擴為 **15 列**(原本只寫了跳脫、四則反例、log 三條,而檔名還寫成 `test_messages.py`);反例由 4 則擴為 **14 則**;§5 覆蓋現況加**安全層 41 項**;新增 §5.9 突變檢查 **19/19**。🔴 兩條原規格裡沒有、而壞掉不會有錯誤訊息的:①**輸出側也要擋**(寫入側白名單是 T10 才加的,之前寫進去的列全都繞過了它),②**算繪後不得有行內樣式屬性** —— nonce 只對元素有效,`style-src 'nonce-…'` 會把 `style="…"` 屬性整批擋掉,**「`<style>` 帶了 nonce」不等於「樣式會生效」**。🔴 §5.9 記下量測工具第三次出問題,且這次會產生**假 ✅**:`-k` 打錯時 pytest 回 rc=5/4 也是非零,而腳本把非零一律讀成「抓到」 |
| v1.8 | 2026-08-25 | Benny | **T09 完工回寫**:§3 的 T09 由 2 列擴為 **11 列**(原本只寫了「非 announcer 403」與「過期不出現」兩條,而檔名還寫成 `test_messages.py`);§5 覆蓋現況加**公告層 21 項**;新增 §5.8 突變檢查 **17/17**。🔴 兩條在原規格裡不存在、而壞掉不會有錯誤訊息的:①**未來的公告也不得出現**(只驗 `ends_at` 是最容易漏的一半),②**甲讀過不得讓乙變已讀**——而該項的突變結果更值得記:把 `user_sub` 從 JOIN 的 ON 搬到 WHERE,症狀不是「已讀錯了」而是**別人一讀那則公告就從我的清單裡消失**,而消失的東西沒有人會回報。⚠ 第一輪有 1 項顯示未抓到,查為 **sed 沒真的改到語意**(加了恆真項而條件還在)——與 T06 的「拿掉 no-store」同一種誤判;腳本已加「突變沒套用就明講」的偵測 |
| v1.7 | 2026-08-25 | Benny | **T08 完工回寫**:§5 覆蓋現況加收件匣層 14 項;§5.7 突變檢查 **9/9**。🔴 記一個**測試的洞**:「零人名」原本只驗 HTML 頁面,而往 API 回應加姓名欄位**測試照樣綠**(模板沒算繪它,但前端 JS 拿得到,而 §4.2a L1 禁的是「一般使用者可見的面」)——已補「API 回應不得含姓名類欄位」的結構性斷言。⚠ 另記**量測工具**的第二次問題:突變腳本的還原不是原子的,同一秒內完成時 Python 會沿用由突變後原始碼編出的 `.pyc`,使突變留在工作目錄而 `git status` 看不出來;已加 `__pycache__` 清除,並作為「突變腳本不進 repo」的第二個理由 |
| v1.6 | 2026-08-24 | Benny | **T07 完工回寫**:新增 9 支 schema 測試規格(`test_schema.py`),逐支寫出「壞掉時沒有症狀」的理由;§5 覆蓋現況加 schema 層 10 項;§5.6 突變檢查 **9/9 全被抓到**。🔴 其中 `test_models_match_migration_schema` **補 T05 誠實標註的盲點**——`create_all()` 與 migration 的 schema 在此之前從來沒有人比對過,而漂移的症狀是「本機全綠、部署後少一個欄位」;`test_message_recipient_sub_has_no_fk` 則是**斷言一個約束不存在**,擋的是「日後有人順手補上外鍵」那個看起來在修缺漏、實際把能力靜默關掉的動作 |
| v1.5 | 2026-08-24 | Benny | **T05 完工回寫**:T05 規格由 4 支擴為 **13 支 + migration 3 支**(檔案 `test_authz.py` / `test_migration.py`),逐支寫出「壞掉時沒有症狀」的理由;§5 覆蓋現況新增授權層與 migration 層,並**誠實標明 PG15 未演練**(本機只有 PG16.13,無 docker daemon);§5.4 突變檢查 **10/10 全被抓到**;§5.5 記兩個**測試自身**的缺陷——🔴 相對路徑讓同一支測試「從 repo 根綠、經 `run_all.sh` 紅」,以及 `test_downgrade_is_not_a_stub` 把 `op.drop_table` 當成 docstring 濾掉而誤判每個正確的 downgrade |
| v1.4 | 2026-08-24 | Benny | **T06 完工回寫**:T06 規格由 3 支擴為 7 支(檔案由 `test_auth.py` 改為 `test_logout.py`)。🔴 **§5.2 記一處規格更正**:原寫「帶斜線變體必須 404」,實測 FastAPI 回 **307 且保留 `sid`/`iss`**,重判後認定那個 redirect 在正式環境是**安全網**(登記錯字仍能生效)而非危害,改成「打登記值必須 204」——不依賴 redirect 行為;同時放棄「列舉 `app.routes`」的做法,因為本版 FastAPI 把 include 的結果包成 `_IncludedRouter`,列舉頂層**一個實際端點都拿不到**。🔴 **§5.3 突變檢查 6/6**,其中「先導 IdP 再清」第一次**未被抓到**——原測試只斷言 /me 回 401,而刪 cookie 也會讓它 401,伺服器端 session 其實還活著。已補「session 數減一」與「重放抄走的 cookie 必須 401」 |
| v1.3 | 2026-08-21 | Benny | **T04 完工回寫**:新增五支測試規格——`test_login_route_issues_pkce_s256`(PKCE 退化成 plain **不會有症狀**)、`test_state_mismatch_is_rejected`(要連「沒建 session」一起斷言)、`test_refresh_failure_logs_user_out`(不然 §3.3 換來的收權即時性是假的)、`test_auth_routes_absent_without_issuer`(回滾閥門)、`test_env_example_oidc_values_match_portal_delivery_shape`(🔴 實際踩到:`.env.example` 與 portal 交付檔的 `INTERNAL_BASE` 形狀不一致,症狀只在第一個真人登入時出現)。並記錄 §5 新增的**突變檢查**結果:9 項突變 8 項被測試抓到 |
| v1.2 | 2026-08-18 | Benny | **T03b 逐條對齊帶進來的五支測試**:①T04 `test_fake_idp_token_carries_real_claims`(契約 v3.2 的 `at_hash` 實例——PLM 400 多支離線測試沒抓到,因為測試自己造的 token 沒那個 claim);②T04 `test_redirect_uri_matches_registered_value`(v2.14:PLM 因 `reverse()` 回後註冊者而首登即 mismatch,而 **app 的 log 是空的**);③T04 手動「壞 code 驗 secret」(v2.17:用必定失敗的請求證明另一件事,不需真人登入);④T06 `test_frontchannel_route_matches_client_registration_exactly`(§10.3a:逐字相同才是規格,帶斜線的變體必須 404);⑤T14 `test_unregistered_azp_is_denied`(§11.5:**內網不是身分**)。並**更正一處**:T14 缺 scope 的預期由 401 改為 **403**——§11.5 明訂兩者分開,混用會讓呼叫方查不出是憑證錯還是授權不足 |
| v1.1 | 2026-08-18 | Benny | **補一個同型盲點與五支 T14 測試**。①§4 新增第 4 條:**假 token 必須帶真實 IdP 會給的 claims,尤其 `at_hash`** ——來源是契約 v3.2 記載的 PLM 實例:開旗標當天第一個真人登入 **100% 失敗於 `at_hash`**,而其 **400 多支離線測試一支都沒抓到,因為測試自己造的 token 沒有那個 claim**。我方原本只釘了「假 token 必須真的會過期」,**兩者是同一個形狀**(測試替身比真實 IdP 寬鬆),故一併釘住。②T14 的五支測試依 portal 2026-08-18 核定的規格具體化:逐端點驗 scope(§11.9 第 2 坑)、`X-User-Id` 必驗不只記錄、`Idempotency-Key` 去重、`source_app` 不採信 body |
| v1.0 | 2026-08-15 | Benny | 初版:測試四分層、架構元件對應、T01–T16 逐任務紅測試規格(含契約踩過的坑:kid 輪替、伺服器端續期、假 token 不得永不過期、bootstrap 對既有 pending 帳號生效、SLO 測試前清 cookie、action_url 前綴比對反例);測試資料原則;CI 覆蓋現況誠實標示未撰寫/未執行項 |
