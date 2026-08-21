# cats-inbox TDD 測試計畫表

**建立日期:** 2026-08-15 09:40
**最後更新:** 2026-08-18 02:55
**版本:** v1.2

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
訊息與公告(業務)                  test_messages.py(T08–T10:deny-by-default/跳脫/白名單)
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
| T05 | `test_auth.py::test_first_login_creates_user_with_sub_only` | 新 sub 首登後 DB 只多一列且**無 email/姓名/密碼欄** | 契約 §4.2 身分落地 |
| T05 | `test_auth.py::test_zero_role_user_gets_403_with_own_sub_shown` | 無角色打業務 API → 403;待開通頁含本人 `sub` | deny-by-default + 雞生蛋解法(§4.3) |
| T05 | `test_auth.py::test_bootstrap_admin_applies_to_existing_pending_user` | **已存在的 pending 帳號**寫進清單後再登入→升級;已 disabled 者不得復活;重複登入 idempotent | 🔴 upload-program 踩過:只在「建號當下」比對,對第一個管理員**永遠不會生效** |
| T05 | `test_auth.py::test_display_name_cache_never_in_authz_path` | 授權判定不得讀 `display_name`(源碼層檢查);快取清除工具可清孤兒列 | 契約 §4.2a L1 第 4/7 條 |
| T06 | `test_auth.py::test_frontchannel_logout_is_idempotent_and_unauthenticated` | 免認證呼叫 → 204;重複呼叫 → 204;帶/不帶 `sid`+`iss` 皆 204 | 契約 §10.3;iframe 載入時不會帶 token |
| T06 | `test_auth.py::test_frontchannel_logout_only_deletes_cookie` | 端點不得建立 session、不得寫業務資料 | §10.3「被任意第三方呼叫的最壞後果必須是使用者被登出」 |
| T06 | `test_auth.py::test_frontchannel_route_matches_client_registration_exactly` | route 字串必須是 `/inbox/oidc/frontchannel-logout`(**無結尾斜線**);帶斜線的變體必須 **404**(證明沒有靜默 redirect) | 🔴 §10.3a:Keycloak 拿登記值去**呼叫**而非比對,「無斜線」不是規定、**逐字相同**才是。差一字元的症狀是**端點零呼叫**,與「設定沒套用」完全相同(PLM 2026-08-14 前例;T03b 補) |

### M3 通知核心(測試待寫)

| 任務 | 紅測試 | 斷言 | 為什麼 |
|---|---|---|---|
| T07 | `test_skeleton.py::test_migration_chain_reversible` | 每條 migration 有 backward;up→down→up 演練通過 | 共通紅線:動資料的 migration 必須可回滾 |
| T07 | `test_messages.py::test_schema_has_no_pii_columns` | 表結構無 email/姓名/密碼欄(`display_name` 除外且 nullable) | 契約 §4.2 |
| T08 | `test_messages.py::test_cannot_read_other_users_messages` | 讀 `recipient_sub != 自己` → **403** | deny-by-default 的核心;這是本系統最重要的一條 |
| T08 | `test_messages.py::test_sender_sub_ignores_client_input` | 前端傳 `sender_sub` 一律被忽略,以 token 的 sub 為準 | 寄件人不可偽造(A.3) |
| T08 | `test_messages.py::test_mark_read_is_idempotent` | 重複標已讀 → 同樣 200,`read_at` 不變 | 輪詢客戶端會重送 |
| T09 | `test_messages.py::test_non_announcer_cannot_publish` | 無 `announcer` 角色發公告 → 403 | 公告是廣播,權限錯的半徑最大 |
| T09 | `test_messages.py::test_expired_announcement_not_in_active` | `ends_at` 已過的公告不得出現在 active | |
| T10 | `test_messages.py::test_body_is_escaped_on_output` | 送入 `<script>alert(1)</script>`,輸出必須是跳脫後的字面文字 | 🔴 R1:同源之下一次 stored XSS 可觸及 IdP |
| T10 | `test_messages.py::test_action_url_whitelist_rejects` | **反例四則**:外部網址、`javascript:`、協定相對 `//evil.tld`、`https://catsapp.sporton.com.tw.evil.tld/` → 一律 **400** | 🔴 R2:站內通知是現成的釣魚載具;第四個反例是前綴比對的經典漏洞 |
| T10 | `test_messages.py::test_subject_and_body_never_logged` | log 輸出不得含主旨/內容 | A.3:log 只記 id、sub、事件類型 |

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
| 文件層 21 項 | ✅ 已跑、全綠 |
| 骨架層 19 項 | ✅ 已跑、全綠 |
| 應用層 4 項 | ✅ 已跑、全綠 |
| M2–M6 測試 | ⬜ **尚未撰寫**(規格見 §3;不得視為已覆蓋)。⚠ T14 的規格已於 2026-08-18 由 portal 核定,五支測試的斷言已具體化 |
| 真 IdP / gateway 冒煙 | ⬜ **尚未執行**(需 T03 client 與 T11 路由;CI 跑不動) |
| `docker compose config` 解析 | ✅ 已於本機驗過(Compose v5.1.1);**容器實際啟動尚未驗**(本環境無 docker daemon) |

---

## 版本歷史

| 版本 | 日期 | 修改人 | 摘要 |
|---|---|---|---|
| v1.2 | 2026-08-18 | Benny | **T03b 逐條對齊帶進來的五支測試**:①T04 `test_fake_idp_token_carries_real_claims`(契約 v3.2 的 `at_hash` 實例——PLM 400 多支離線測試沒抓到,因為測試自己造的 token 沒那個 claim);②T04 `test_redirect_uri_matches_registered_value`(v2.14:PLM 因 `reverse()` 回後註冊者而首登即 mismatch,而 **app 的 log 是空的**);③T04 手動「壞 code 驗 secret」(v2.17:用必定失敗的請求證明另一件事,不需真人登入);④T06 `test_frontchannel_route_matches_client_registration_exactly`(§10.3a:逐字相同才是規格,帶斜線的變體必須 404);⑤T14 `test_unregistered_azp_is_denied`(§11.5:**內網不是身分**)。並**更正一處**:T14 缺 scope 的預期由 401 改為 **403**——§11.5 明訂兩者分開,混用會讓呼叫方查不出是憑證錯還是授權不足 |
| v1.1 | 2026-08-18 | Benny | **補一個同型盲點與五支 T14 測試**。①§4 新增第 4 條:**假 token 必須帶真實 IdP 會給的 claims,尤其 `at_hash`** ——來源是契約 v3.2 記載的 PLM 實例:開旗標當天第一個真人登入 **100% 失敗於 `at_hash`**,而其 **400 多支離線測試一支都沒抓到,因為測試自己造的 token 沒有那個 claim**。我方原本只釘了「假 token 必須真的會過期」,**兩者是同一個形狀**(測試替身比真實 IdP 寬鬆),故一併釘住。②T14 的五支測試依 portal 2026-08-18 核定的規格具體化:逐端點驗 scope(§11.9 第 2 坑)、`X-User-Id` 必驗不只記錄、`Idempotency-Key` 去重、`source_app` 不採信 body |
| v1.0 | 2026-08-15 | Benny | 初版:測試四分層、架構元件對應、T01–T16 逐任務紅測試規格(含契約踩過的坑:kid 輪替、伺服器端續期、假 token 不得永不過期、bootstrap 對既有 pending 帳號生效、SLO 測試前清 cookie、action_url 前綴比對反例);測試資料原則;CI 覆蓋現況誠實標示未撰寫/未執行項 |
