# -*- coding: utf-8 -*-
"""T14a 推送 API 紅測試(S2S 驗證 / 逐端點 scope / 來源登記 / X-User-Id / 去重)。

對應驗收:`docs/任務表.md` T14a、`docs/TDD測試計畫表.md` §3 的 T14 六支、
`docs/dev-logs/2026-09-23_T14a_推送API本體.md` 的 17 條。
規格權威:portal 2026-08-18 核定(契約 §11.7 第一案)+ 我方 2026-08-24 答覆 Q2/Q3。

🔴 這一組測試的共同點:**每一種失敗都不會有錯誤訊息。**

| 規格 | 壞掉時的症狀 |
|---|---|
| 使用者的 token 不是服務憑證 | id_token 的 `aud` **正好是 `cats-inbox`**;只驗 `aud` 的話,任何登入過的人都能以系統身分發通知 |
| 401 與 403 分開 | 呼叫方分不出「憑證錯」還是「沒被授權」,往錯的方向查一整天 |
| 內網不是身分 | 「從內網來的就放行」= 任何打進內網的東西都能發官方通知 |
| `X-User-Id` 必驗 | 稽核鏈在最需要的時候恰好是空的 |
| 去重 | 重試讓同一則通知出現兩次;而「同 key 不同內容」若被當成重送,第二則會**默默消失** |
| `source_app` 不採信 body | 能自稱來源 = 能冒充任何系統,而通知帶著平台的官方外觀 |

⚠ 本檔的 token 一律由 `tests/fake_idp.py` 以**真的 RSA 金鑰**簽出,並帶滿真實
Keycloak client_credentials token 會給的 claims(替身比真實寬鬆會抹平缺陷,契約 v3.2)。
"""

from __future__ import annotations

import json
import re
import uuid

from tests.conftest import _login
from tests.fake_idp import CLIENT_ID, S2S_AZP, S2S_SA_SUB

PUSH = "/inbox/api/v1/notifications"
SOURCES = "/inbox/admin/sources"

# 收件人 = 替身登入時的那個 sub —— 推送後可以真的登入去看(端到端)
RECIPIENT = "11111111-2222-3333-4444-555555555555"
# X-User-Id:觸發這個事件的那個人(稽核鏈),不是收件人、也不是呼叫方自己
ACTOR = "22222222-3333-4444-8555-666666666666"
OTHER_ACTOR = "33333333-4444-4555-8666-777777777777"
OTHER_AZP = "rf-workorder-sa"


# ─────────────────────────────────────────────────────────────────────
# 輔助
# ─────────────────────────────────────────────────────────────────────
def _register(db_session, azp: str = S2S_AZP, label: str = "TRF 系統", *, enabled: bool = True):
    """把一個來源登記進 `source_app`(走 repo 的公開函式,不直接塞列)。"""
    from app.repo import register_source_app, set_source_app_enabled

    register_source_app(db_session, azp=azp, label=label, created_by="test")
    if not enabled:
        set_source_app_enabled(db_session, azp=azp, enabled=False)
    db_session.commit()


def _token(idp, clock, **kw) -> str:
    """簽一張 client_credentials 的 access token(預設:已登記的 compliance-sa + 正確 scope)。"""
    return idp.access_token(now=clock(), **kw)


def _headers(token: str | None = None, *, actor: str | None = ACTOR,
             key: str | None = "evt-0001", extra: dict | None = None) -> dict:
    """組推送請求的標頭;傳 None 即「不帶這個標頭」。"""
    h: dict = {}
    if token is not None:
        h["Authorization"] = f"Bearer {token}"
    if actor is not None:
        h["X-User-Id"] = actor
    if key is not None:
        h["Idempotency-Key"] = key
    h.update(extra or {})
    return h


def _payload(**kw) -> dict:
    p = {
        "recipient_sub": RECIPIENT,
        "subject": "TRF-123 已退回",
        "body": "請補件後重送。",
        "action_url": "/compliance/trf/123",
    }
    p.update(kw)
    return p


def _count(db_session, model_name: str = "Message") -> int:
    """數某張表的列數(一律重新查,不用 session 裡的舊物件)。"""
    import app.models as models
    from sqlalchemy import func, select

    db_session.expire_all()
    return int(db_session.scalar(select(func.count()).select_from(getattr(models, model_name))))


def _message(db_session, message_id: str):
    from app.models import Message

    db_session.expire_all()
    return db_session.get(Message, uuid.UUID(message_id))


def _grant(db_session, role: str):
    from app.repo import grant_role

    grant_role(db_session, RECIPIENT, role, granted_by="test")
    db_session.commit()


def _csrf_of_page(http, path: str) -> str:
    html = http.get(path).text
    m = re.search(r'name="csrf_token"\s+value="([0-9a-f]{64})"', html)
    assert m, f"🔴 {path} 沒有有效的 csrf_token hidden input"
    return m.group(1)


def _csrf_for_current_session(http) -> str:
    """直接由 session store 算出這個 session 的 CSRF token。

    用途:讓「沒有權限的人」也帶著**正確的** CSRF token 送表單 ——
    這樣拿到的 403 只可能來自能力判定,而不是 CSRF(兩者都是 403)。
    """
    from app.session import SESSION_COOKIE

    store = http.app.state.session_store
    key = store.unseal(http.cookies.get(SESSION_COOKIE))
    assert key, "目前沒有 session(測試本身寫錯:應先 _login)"
    return store.csrf_token(key)


# ═══════════════════════════════════════════════════════════════════
# 1. 🔴 認證:只有服務憑證進得來(401)
# ═══════════════════════════════════════════════════════════════════
def test_push_requires_s2s_token(app_client, db_session, idp, clock):
    """無 token / 使用者的 token / 錯 `aud` / 過期 / 錯演算法 / 錯 `iss` → 一律 **401**。

    🔴 **先登入再打** —— 每一個案例都帶著一個**有效的使用者 session cookie**。
       推送端點必須完全不理會它:瀏覽器的 session 不是服務憑證。

    🔴 「使用者的 id_token」那一格是這支測試存在的主要理由:
       Keycloak 簽給我方的 id_token,`aud` **正好就是 `cats-inbox`**、簽章也是真的。
       只驗簽章 + `aud` 的實作會放它進來。擋住它的是**兩道各自獨立**的關:
       `typ`(id_token 是 `ID`)與「`azp` 是我方自己」(id_token 的 `azp` 就是我方)。
       ⚠ 因為兩道都擋得住它,這一格**單獨拿掉任一道都不會紅** —— 所以下面另有
         `typ=ID` / `typ=Refresh`(只有 `typ` 擋得住)與「azp 是我方自己」
         (只有 azp 那道擋得住)各自隔出來。
    """
    http, transport = app_client
    _login(http, transport)
    _register(db_session)

    cases = {
        "無 token(只有使用者的 session cookie)": None,
        "使用者的 id_token(aud 正好是 cats-inbox)": idp.id_token(now=clock()),
        "使用者的 access token(登入流程簽給 cats-inbox 的)": idp.user_access_token(now=clock()),
        "azp 是我方自己(即使 aud 與 scope 都對)": _token(idp, clock, azp=CLIENT_ID),
        # 🔴 下面兩格把 `typ` 那一關**單獨**隔出來:上面的 id_token 同時也會被
        #    「azp 是我方自己」擋下(id_token 的 azp 就是我方),只靠它的話,
        #    拿掉 `typ` 檢查不會有任何一支測試變紅(突變檢查實際抓到的盲點)。
        "typ=ID(其餘全對:別的 client、aud 與 scope 都對)": _token(idp, clock, typ="ID"),
        "typ=Refresh(其餘全對)": _token(idp, clock, typ="Refresh"),
        "錯 aud(簽給別的服務的 S2S token)": _token(idp, clock, aud=("rf-workorder", "account")),
        "過期 31 秒(超過 ±30s 容忍)": _token(idp, clock, expires_in=-31),
        "HS256(對稱簽章,知道 secret 就能自簽)": _token(idp, clock, alg="HS256"),
        "alg=none": _token(idp, clock, alg="none"),
        "錯 iss": _token(idp, clock, iss="https://evil.tld/auth/realms/sporton"),
        "缺 azp": _token(idp, clock, omit=("azp",)),
        "不是 JWT": "not-a-jwt",
    }
    for i, (why, tok) in enumerate(cases.items()):
        r = http.post(PUSH, json=_payload(), headers=_headers(tok, key=f"k-{i}"))
        assert r.status_code == 401, f"{why}:預期 401,實得 {r.status_code} {r.text[:200]}"
        # RFC 6750 §3:401 要告訴呼叫方「用 Bearer」—— 不帶的話它不知道該換什麼
        assert r.headers.get("www-authenticate", "").startswith("Bearer"), (
            f"{why}:401 沒有 WWW-Authenticate: Bearer"
        )
    assert _count(db_session) == 0, "🔴 401 卻寫入了訊息"

    # 正向對照:同一個 client、同一組標頭,token 對了就要進得來。
    # ⚠ 沒有這一條的話,「一律 401」的實作會讓上面全綠。
    # 過期 29 秒 = 仍在 ±30s 容忍內(契約 §3.3:容忍是**驗證方**的義務)
    ok = http.post(PUSH, json=_payload(),
                   headers=_headers(_token(idp, clock, expires_in=-29), key="k-ok"))
    assert ok.status_code == 201, f"正向對照失敗:{ok.status_code} {ok.text[:200]}"


# ═══════════════════════════════════════════════════════════════════
# 2. 🔴 逐端點驗 scope(403,不是 401)
# ═══════════════════════════════════════════════════════════════════
def test_push_verifies_scope_per_endpoint(app_client, db_session, idp, clock):
    """`aud` 正確但沒有 `notification:push` → **403 `insufficient_scope`**。

    🔴 §11.5:401 = 憑證無效、403 = 憑證有效但無此權限 —— 混用會讓呼叫方
       查不出是憑證錯還是授權不足。§11.9 第 2 坑:只驗 `aud` 的話,
       兩條流的 token 互打得動,**而兩邊的測試都會過**。
    🔴 scope 比對必須是**整詞**:子字串或前綴比對會讓 `notification:pushall`、
       `notification:push:admin` 也算數 —— 而那是另一個(或不存在的)權限。
    """
    http, _ = app_client
    _register(db_session)

    cases = {
        "完全沒有 scope claim": dict(omit=("scope",)),
        "空 scope": dict(scope=""),
        "只有 OIDC 預設 scope": dict(scope="profile email"),
        "別的端點的 scope": dict(scope="workorder:read profile"),
        "子字串 notification:pushall": dict(scope="notification:pushall profile"),
        "前綴 notification:push:admin": dict(scope="notification:push:admin"),
        "大小寫不同": dict(scope="Notification:Push"),
    }
    for i, (why, kw) in enumerate(cases.items()):
        r = http.post(PUSH, json=_payload(),
                      headers=_headers(_token(idp, clock, **kw), key=f"k-{i}"))
        assert r.status_code == 403, f"{why}:預期 403,實得 {r.status_code} {r.text[:200]}"
        assert r.json().get("error") == "insufficient_scope", f"{why}:{r.json()}"
        assert 'scope="notification:push"' in r.headers.get("www-authenticate", ""), (
            f"{why}:403 應告訴呼叫方缺的是哪個 scope(RFC 6750 §3)"
        )
    assert _count(db_session) == 0, "🔴 403 卻寫入了訊息"

    ok = http.post(PUSH, json=_payload(), headers=_headers(_token(idp, clock), key="k-ok"))
    assert ok.status_code == 201, f"正向對照失敗:{ok.status_code} {ok.text[:200]}"


# ═══════════════════════════════════════════════════════════════════
# 3. 🔴 deny-by-default:未登記 / 已停用的來源(403)
# ═══════════════════════════════════════════════════════════════════
def test_unregistered_azp_is_denied(app_client, db_session, idp, clock):
    """憑證與 scope 都對,但 `azp` 不在登記表 → **403**;停用的也是 403。

    🔴 §11.5 第 3 條逐字:「**內網不是身分**」。帶上看起來像內網的來源位址
       (`X-Forwarded-For` / `X-Real-IP`)**不得改變任何判定** —— 本端點根本不看位址。
    """
    http, _ = app_client
    _register(db_session)                          # 只登記 compliance-sa
    stranger = _token(idp, clock, azp="unknown-sa")

    for i, extra in enumerate(({}, {"X-Forwarded-For": "172.18.0.5", "X-Real-IP": "172.18.0.5"})):
        r = http.post(PUSH, json=_payload(), headers=_headers(stranger, key=f"k-{i}", extra=extra))
        assert r.status_code == 403, f"未登記的 azp 應 403,實得 {r.status_code}(extra={extra})"
        assert r.json().get("error") == "source_not_registered", r.json()

    _register(db_session, azp=OTHER_AZP, label="工單系統", enabled=False)
    r = http.post(PUSH, json=_payload(),
                  headers=_headers(_token(idp, clock, azp=OTHER_AZP), key="k-disabled"))
    assert r.status_code == 403, f"停用的來源應 403,實得 {r.status_code}"
    assert r.json().get("error") == "source_disabled", r.json()

    assert _count(db_session) == 0, "🔴 403 卻寫入了訊息"


# ═══════════════════════════════════════════════════════════════════
# 4. 🔴 X-User-Id 必驗(不只記錄)
# ═══════════════════════════════════════════════════════════════════
def test_x_user_id_is_verified_not_just_logged(app_client, db_session, idp, clock):
    """缺 / 空 / 非 UUID / 等於呼叫方自身 → **400 且零列**;合法者寫進稽核收據。

    🔴 portal 加嚴條件:不得「缺就當系統發出」—— 那會讓稽核鏈在最需要的時候
       恰好是空的。「等於呼叫方自身」= 服務把自己的 service account 當成觸發者,
       同樣等於沒有稽核。
    """
    http, _ = app_client
    _register(db_session)
    tok = _token(idp, clock)

    cases = {
        "缺": None,
        "空字串": "",
        "帳號名": "jdoe",
        "email": "jdoe@sporton.com.tw",
        "UUID 少一段": "22222222-3333-4444-8555",
        "UUID 夾大括號": "{22222222-3333-4444-8555-666666666666}",
        "UUID 沒有連字號": "22222222333344448555666666666666",
        "等於呼叫方的 service account": S2S_SA_SUB,
        "等於呼叫方的 service account(大寫)": S2S_SA_SUB.upper(),
    }
    for i, (why, actor) in enumerate(cases.items()):
        r = http.post(PUSH, json=_payload(), headers=_headers(tok, actor=actor, key=f"k-{i}"))
        assert r.status_code == 400, f"X-User-Id {why}:預期 400,實得 {r.status_code} {r.text[:200]}"
        assert "x_user_id" in r.json().get("error", ""), f"X-User-Id {why}:錯誤碼 {r.json()}"
    assert _count(db_session) == 0, "🔴 400 卻寫入了訊息"
    assert _count(db_session, "PushReceipt") == 0

    # 合法的觸發者:寫進稽核收據(不只是 log)
    r = http.post(PUSH, json=_payload(), headers=_headers(tok, actor=ACTOR.upper(), key="k-ok"))
    assert r.status_code == 201, r.text[:200]
    from app.models import PushReceipt
    from sqlalchemy import select

    db_session.expire_all()
    receipt = db_session.scalar(select(PushReceipt))
    assert receipt.actor_sub == ACTOR, f"收據的 actor_sub 應為正規化後的小寫 UUID,實得 {receipt.actor_sub}"
    assert receipt.azp == S2S_AZP
    assert str(receipt.message_id) == r.json()["id"]


# ═══════════════════════════════════════════════════════════════════
# 5. 🔴 Idempotency-Key 去重(24h 窗口)
# ═══════════════════════════════════════════════════════════════════
def test_idempotency_key_dedupes(app_client, db_session, idp, clock):
    """同一把 key 重送 → **200、同一個 id、訊息數不變**;缺 key 或壞 key → 400。"""
    http, _ = app_client
    _register(db_session)
    tok = _token(idp, clock)

    r1 = http.post(PUSH, json=_payload(), headers=_headers(tok, key="evt-42"))
    assert r1.status_code == 201, r1.text[:200]
    r2 = http.post(PUSH, json=_payload(), headers=_headers(tok, key="evt-42"))
    assert r2.status_code == 200, f"重送應 200,實得 {r2.status_code} {r2.text[:200]}"
    assert r2.json()["id"] == r1.json()["id"], "🔴 重送回了不同的 id"
    assert r2.headers.get("idempotent-replayed") == "true", "重送應標明是回放"
    assert r1.headers.get("idempotent-replayed") != "true"
    assert _count(db_session) == 1, "🔴 重送新增了第二則訊息"

    for i, key in enumerate((None, "", "x" * 129, "has space")):
        r = http.post(PUSH, json=_payload(), headers=_headers(tok, key=key))
        assert r.status_code == 400, f"Idempotency-Key={key!r}:預期 400,實得 {r.status_code}"
        assert "idempotency_key" in r.json().get("error", ""), r.json()
    assert _count(db_session) == 1


def test_idempotency_window_is_24_hours(app_client, db_session, idp, clock):
    """窗口 **24 小時**:未滿 → 回放;滿 24 小時 → **視為新訊息**(Q3 答覆)。

    🔴 超窗**不得拒收**:拒收會讓一個月後的重跑靜默失敗,而「通知沒送到」是
       本系統最糟的失效模式 —— 寧可重複一次,也不要靜默丟掉。
    ⚠ 超窗之後,同一把 key 的回放對象要換成**新的那一則**。
    """
    http, _ = app_client
    _register(db_session)

    r1 = http.post(PUSH, json=_payload(), headers=_headers(_token(idp, clock), key="evt-7"))
    assert r1.status_code == 201

    clock.advance(24 * 3600 - 1)
    r2 = http.post(PUSH, json=_payload(), headers=_headers(_token(idp, clock), key="evt-7"))
    assert r2.status_code == 200 and r2.json()["id"] == r1.json()["id"], (
        f"23:59:59 應回放同一則,實得 {r2.status_code} {r2.text[:200]}"
    )

    clock.advance(1)                                   # 正好 24 小時
    r3 = http.post(PUSH, json=_payload(), headers=_headers(_token(idp, clock), key="evt-7"))
    assert r3.status_code == 201, f"滿 24 小時應視為新訊息,實得 {r3.status_code} {r3.text[:200]}"
    assert r3.json()["id"] != r1.json()["id"]
    assert _count(db_session) == 2

    r4 = http.post(PUSH, json=_payload(), headers=_headers(_token(idp, clock), key="evt-7"))
    assert r4.status_code == 200 and r4.json()["id"] == r3.json()["id"], (
        "🔴 超窗重建之後,回放的應是新的那一則"
    )
    assert _count(db_session) == 2


def test_same_key_with_different_payload_is_422(app_client, db_session, idp, clock):
    """24h 內同一把 key、**不同內容** → **422 且不新增**。

    🔴 回放的話,呼叫方拿到 200 而**第二則通知根本不存在** —— 呼叫方把 key 重用在
       另一個事件上(常見的整合 bug:用固定字串或時間戳當 key)時,那些通知會
       一則一則默默消失。IETF `draft-ietf-httpapi-idempotency-key-header` 的建議碼是 422。
    ⚠ 觸發者(`X-User-Id`)也是請求內容的一部分:同一把 key 換了觸發者,
      就不是「同一個請求的重送」。
    """
    http, _ = app_client
    _register(db_session)
    tok = _token(idp, clock)

    r1 = http.post(PUSH, json=_payload(subject="A"), headers=_headers(tok, key="evt-9"))
    assert r1.status_code == 201

    variants = {
        "主旨不同": dict(payload=_payload(subject="B"), actor=ACTOR),
        "內容不同": dict(payload=_payload(subject="A", body="另一段"), actor=ACTOR),
        "收件人不同": dict(payload=_payload(subject="A", recipient_sub=ACTOR), actor=ACTOR),
        "action_url 不同": dict(payload=_payload(subject="A", action_url="/plm/"), actor=ACTOR),
        "觸發者不同": dict(payload=_payload(subject="A"), actor=OTHER_ACTOR),
    }
    for why, v in variants.items():
        r = http.post(PUSH, json=v["payload"], headers=_headers(tok, actor=v["actor"], key="evt-9"))
        assert r.status_code == 422, f"{why}:預期 422,實得 {r.status_code} {r.text[:200]}"
        assert r.json().get("error") == "idempotency_key_reused", r.json()
    assert _count(db_session) == 1, "🔴 422 卻新增了訊息"


def test_idempotency_keys_are_scoped_per_caller(app_client, db_session, idp, clock):
    """兩個來源用**同一把 key** → 各自獨立,互不回放。

    🔴 key 若是全域的,B 系統的推送會被「回放」成 A 系統的那一則 ——
       B 的收件人收不到自己的通知,而 A 的內容以 200 回給了 B(**跨來源外洩**)。
    """
    http, _ = app_client
    _register(db_session, azp=S2S_AZP, label="TRF 系統")
    _register(db_session, azp=OTHER_AZP, label="工單系統")

    ra = http.post(PUSH, json=_payload(subject="A"),
                   headers=_headers(_token(idp, clock, azp=S2S_AZP), key="evt-1"))
    rb = http.post(PUSH, json=_payload(subject="B"),
                   headers=_headers(_token(idp, clock, azp=OTHER_AZP), key="evt-1"))
    assert ra.status_code == 201 and rb.status_code == 201, (ra.text[:200], rb.text[:200])
    assert ra.json()["id"] != rb.json()["id"]
    assert _message(db_session, ra.json()["id"]).source_app == "TRF 系統"
    assert _message(db_session, rb.json()["id"]).source_app == "工單系統"


# ═══════════════════════════════════════════════════════════════════
# 6. 🔴 source_app 一律由 azp 推導,body 的身分欄位一律不採信
# ═══════════════════════════════════════════════════════════════════
def test_source_app_ignores_body(app_client, db_session, idp, clock):
    """body 帶 `source_app` / `category` / `sender_sub` → **全部被忽略**。

    🔴 能自稱來源 = 能冒充任何系統發通知,而通知帶著平台的官方外觀。
       `sender_sub` 同理(A.3:身分欄位一律後端判定,前端傳入的一律不採信)。
    """
    http, _ = app_client
    _register(db_session, label="TRF 系統")

    r = http.post(
        PUSH,
        json=_payload(source_app="portal", category="direct", sender_sub=ACTOR),
        headers=_headers(_token(idp, clock)),
    )
    assert r.status_code == 201, r.text[:200]
    msg = _message(db_session, r.json()["id"])
    assert msg.source_app == "TRF 系統", f"🔴 source_app 取自 body:{msg.source_app!r}"
    assert msg.category == "system", f"🔴 category 取自 body:{msg.category!r}"
    assert msg.sender_sub is None, f"🔴 sender_sub 取自 body:{msg.sender_sub!r}"


# ═══════════════════════════════════════════════════════════════════
# 7. 輸入驗證(400,且零列)
# ═══════════════════════════════════════════════════════════════════
def test_recipient_sub_must_be_a_uuid(app_client, db_session, idp, clock):
    """`recipient_sub` 不是 UUID 形狀 → **400 且零列**;大寫 UUID 正規化成小寫。

    🔴 收件匣以 `sub` 比對收件人。傳 email 或帳號名進來的話會**回 201**,
       而那則訊息**沒有任何人看得到** —— 呼叫方以為送到了,收件人不知道有東西。
       大寫的 UUID 同理(`sub` 是小寫),所以正規化而不是原樣存。
    """
    http, _ = app_client
    _register(db_session)
    tok = _token(idp, clock)

    for i, bad in enumerate(("jdoe", "jdoe@sporton.com.tw", "", "   ", None,
                             "11111111-2222-3333-4444", "not-a-uuid-at-all-0000000000000000")):
        r = http.post(PUSH, json=_payload(recipient_sub=bad), headers=_headers(tok, key=f"k-{i}"))
        assert r.status_code == 400, f"recipient_sub={bad!r}:預期 400,實得 {r.status_code}"
        assert r.json().get("error") == "invalid_recipient_sub", r.json()
    assert _count(db_session) == 0

    upper = "ABCDEF01-2345-4678-89AB-CDEF01234567"
    r = http.post(PUSH, json=_payload(recipient_sub=upper), headers=_headers(tok, key="k-up"))
    assert r.status_code == 201, r.text[:200]
    assert _message(db_session, r.json()["id"]).recipient_sub == upper.lower()


def test_push_rejects_external_action_url(app_client, db_session, idp, clock):
    """外部 `action_url` 經推送端點 → **400 且零列**。

    🔴 T10 把 `action_url` 的關卡設在 `repo.create_message()`,理由是
       「推送 API 動工時,驗證是附帶工作,最容易被忘記」。這一支證明
       T14a 的端點**真的穿過那道關卡**,而不是自己另寫了一條寫入路徑。
    """
    http, _ = app_client
    _register(db_session)
    tok = _token(idp, clock)
    for i, bad in enumerate(("https://evil.tld/", "javascript:alert(1)", "//evil.tld/x",
                             "https://catsapp.sporton.com.tw.evil.tld/")):
        r = http.post(PUSH, json=_payload(action_url=bad), headers=_headers(tok, key=f"k-{i}"))
        assert r.status_code == 400, f"action_url={bad!r}:預期 400,實得 {r.status_code}"
        assert r.json().get("error", "").startswith("action_url"), r.json()
    assert _count(db_session) == 0, "🔴 400 卻寫入了訊息"
    assert _count(db_session, "PushReceipt") == 0, "🔴 400 卻留下了收據(下次重送會被當成回放)"


def test_body_errors_are_400_and_auth_comes_first(app_client, db_session, idp, clock):
    """**先認證、後讀 body**;有憑證之後,body 的任何問題一律 **400**(不是 422)。

    🔴 認證在前:未認證的呼叫方拿到的若是 400/422,它就能拿本端點探測 payload 格式,
       而且「你沒有憑證」這件最重要的事被別的錯誤蓋掉了。
    ⚠ 422 在本端點**只有一個意思**(同 key 不同內容),所以 body 格式錯誤不用它
       —— 對齊 `docs/開發計畫書.md` §3.4 的錯誤語意(400 = 輸入不合法)。
    """
    http, _ = app_client
    _register(db_session)

    bad_json = b'{"recipient_sub": "11111111-2222-3333-4444-555555555555", '
    r = http.post(PUSH, content=bad_json,
                  headers={**_headers(None), "Content-Type": "application/json"})
    assert r.status_code == 401, f"🔴 無 token + 壞 JSON 應 401,實得 {r.status_code}"

    tok = _token(idp, clock)
    r = http.post(PUSH, content=bad_json,
                  headers={**_headers(tok), "Content-Type": "application/json"})
    assert r.status_code == 400 and r.json().get("error") == "invalid_json", r.text[:200]

    cases = {
        "JSON 陣列": ["a"],
        "JSON 字串": "hello",
        "subject 型別錯": _payload(subject=123),
        "body 型別錯": _payload(body={"html": "<b>x</b>"}),
        "空主旨": _payload(subject="   "),
        "空內容": _payload(body=""),
        "主旨超長": _payload(subject="x" * 256),
    }
    for i, (why, body) in enumerate(cases.items()):
        r = http.post(PUSH, json=body, headers=_headers(tok, key=f"k-{i}"))
        assert r.status_code == 400, f"{why}:預期 400,實得 {r.status_code} {r.text[:200]}"

    # 離譜的大小:在解析 JSON 之前就擋(內容上限 20000 字,256 KiB 已綽綽有餘)
    huge = b'{"subject": "' + b"x" * (256 * 1024) + b'"}'
    r = http.post(PUSH, content=huge,
                  headers={**_headers(tok, key="k-huge"), "Content-Type": "application/json"})
    assert r.status_code == 400 and r.json().get("error") == "payload_too_large", r.text[:200]
    assert _count(db_session) == 0


# ═══════════════════════════════════════════════════════════════════
# 8. 端到端:推送的訊息收件人真的看得到
# ═══════════════════════════════════════════════════════════════════
def test_pushed_message_reaches_recipient_inbox(app_client, db_session, idp, clock):
    """推送 → 收件人**第一次**登入 → API 與頁面都看得到,標籤是登記表的 label。

    🔴 只驗 201 的話,「寫到別人名下」「寫進錯的欄位」都是 201。
    ⚠ 推送發生在收件人**從未登入過**的時候 —— `message.recipient_sub` 刻意不設外鍵
      (T07),這一支同時證明那個決定在推送路徑上是對的。
    """
    from app.models import AppUser

    http, transport = app_client
    _register(db_session, label="TRF 系統")
    db_session.expire_all()
    assert db_session.get(AppUser, RECIPIENT) is None, "前提:收件人還沒登入過"

    r = http.post(PUSH, json=_payload(), headers=_headers(_token(idp, clock)))
    assert r.status_code == 201, r.text[:200]
    mid = r.json()["id"]

    _login(http, transport)
    items = http.get("/inbox/api/v1/messages").json()["items"]
    assert [i["id"] for i in items] == [mid], f"收件人看不到推送的訊息:{items}"
    item = items[0]
    assert item["source_app"] == "TRF 系統"
    assert item["category"] == "system"
    assert item["subject"] == "TRF-123 已退回"
    assert item["action_url"] == "/compliance/trf/123"
    assert http.get("/inbox/api/v1/messages/unread-count").json()["unread"] == 1
    page = http.get("/inbox/").text
    assert "TRF 系統" in page and "TRF-123 已退回" in page


# ═══════════════════════════════════════════════════════════════════
# 9. log:稽核欄位在、內容與 token 不在
# ═══════════════════════════════════════════════════════════════════
def test_push_log_has_audit_fields_but_no_content_or_token(app_client, db_session, idp, clock, capsys):
    """成功與拒絕都留一行單行 JSON:有 `azp` / 觸發者 / `message_id`,**沒有**主旨、內容、token。

    🔴 A.3:訊息主旨與內容不進 log;共通紅線:不記完整 token。
       而**拒絕也要留痕** —— 否則「對方說推了、我們這邊什麼都沒有」查不出是哪一關擋的。
    """
    http, _ = app_client
    _register(db_session)
    tok = _token(idp, clock)
    secret_subject, secret_body = "機密主旨ZZTOP", "機密內容QQPLM"

    capsys.readouterr()
    r = http.post(PUSH, json=_payload(subject=secret_subject, body=secret_body),
                  headers=_headers(tok))
    assert r.status_code == 201
    # 兩種拒絕各打一次:憑證層(403,`app/s2s.py`)與端點層(400,本端點)
    bad = _token(idp, clock, scope="profile")
    http.post(PUSH, json=_payload(subject=secret_subject), headers=_headers(bad, key="k-bad"))
    http.post(PUSH, json=_payload(body=secret_body), headers=_headers(tok, actor=None, key="k-noactor"))
    out = capsys.readouterr().out

    for secret in (secret_subject, secret_body, tok, bad, tok.split(".")[2]):
        assert secret not in out, f"🔴 log 出現了不該出現的東西:{secret[:24]}…"
    events = [json.loads(ln) for ln in out.splitlines() if ln.strip()]
    pushed = [e for e in events if e.get("event") == "notification_pushed"]
    assert len(pushed) == 1, f"應有一筆 notification_pushed:{events}"
    e = pushed[0]
    assert e.get("azp") == S2S_AZP and e.get("actor_sub") == ACTOR
    assert e.get("message_id") == r.json()["id"] and e.get("recipient_sub") == RECIPIENT
    # 憑證層的拒絕記 `s2s_rejected`(通用於日後任何 S2S 端點),端點層的記 `push_rejected`
    s2s = [e for e in events if e.get("event") == "s2s_rejected"]
    assert s2s and s2s[-1].get("reason") == "insufficient_scope", events
    assert s2s[-1].get("azp") == S2S_AZP, "拒絕紀錄要能說出是哪個呼叫方"
    rejected = [e for e in events if e.get("event") == "push_rejected"]
    assert rejected and rejected[-1].get("reason") == "missing_x_user_id", events


# ═══════════════════════════════════════════════════════════════════
# 10. 並行的同 key:撞唯一約束 → 回放,零重複
# ═══════════════════════════════════════════════════════════════════
def test_concurrent_duplicate_key_is_replayed_not_duplicated(app_client, db_session, idp, clock, monkeypatch):
    """模擬「查的那一刻對方還沒寫入」:第二個請求撞 `(azp, key)` 唯一約束 → **回放 200**。

    🔴 先查後寫之間有空窗,真實的重試風暴一定會撞到它。撞到時若回 500,
       呼叫方會**再重試一次** —— 而那次會成功回放,但中間那個 500 已經讓
       對方的告警響了。撞到時若沒有回滾,則會多出一則沒有收據的重複訊息。
    """
    import app.repo as repo

    http, _ = app_client
    _register(db_session)
    tok = _token(idp, clock)
    r1 = http.post(PUSH, json=_payload(), headers=_headers(tok, key="evt-race"))
    assert r1.status_code == 201

    real = repo.find_push_receipt
    calls = {"n": 0}

    def racing(session, *, azp, idempotency_key):
        calls["n"] += 1
        if calls["n"] == 1:
            return None          # 查的那一刻,「另一個請求」的收據還沒 commit
        return real(session, azp=azp, idempotency_key=idempotency_key)

    monkeypatch.setattr(repo, "find_push_receipt", racing)
    r2 = http.post(PUSH, json=_payload(), headers=_headers(tok, key="evt-race"))
    assert calls["n"] >= 2, "替身沒有被呼叫到 —— 這支測試沒有模擬到空窗"
    assert r2.status_code == 200, f"撞唯一約束應回放 200,實得 {r2.status_code} {r2.text[:200]}"
    assert r2.json()["id"] == r1.json()["id"]
    assert _count(db_session) == 1, "🔴 撞約束之後留下了重複的訊息(沒有回滾)"


# ═══════════════════════════════════════════════════════════════════
# 11. 🔴 列舉式守門:所有寫入路由,匿名一律 401/403
# ═══════════════════════════════════════════════════════════════════
def test_every_write_route_rejects_anonymous(app_client):
    """**列舉**所有 POST/PUT/PATCH/DELETE 路由:不帶任何憑證 → 一律 401 或 403。

    🔴 A.3「**無驗證的推送端點不得部署到正式環境**」在此之前只是一句話。
       逐支列出的話,下一個忘了掛認證的寫入端點不會被列進去 —— 而它**照樣能用**,
       只是誰都能用。與 T10 的 CSP、T10b 的 CSRF 是同一個形狀。
    """
    from tests.conftest import iter_routes

    http, _ = app_client
    checked = []
    for path, methods, _endpoint in iter_routes(http.app):
        for method in sorted(methods & {"POST", "PUT", "PATCH", "DELETE"}):
            url = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), path)
            r = http.request(method, url, follow_redirects=False)
            assert r.status_code != 404, f"🔴 {method} {path} 打不到 —— iter_routes 算錯前綴"
            assert r.status_code in (401, 403), (
                f"🔴 {method} {path} 匿名請求得到 {r.status_code}:{r.text[:120]}"
            )
            checked.append(path)
    # 列舉不到東西時這支會全綠而什麼都沒驗(D01 / T10b 記過的空過)
    assert len(checked) >= 9, f"只列舉到 {len(checked)} 支寫入路由:{checked}"
    assert PUSH in checked, f"🔴 推送端點沒有被列舉到:{checked}"


def test_openapi_documents_the_push_contract(app_client):
    """`/inbox/openapi.json` 要寫出推送端點的 body 欄位、兩個必帶標頭與六種回應。

    🔴 `docs/開發計畫書.md` §3.4:「實作時以 OpenAPI 為權威」。本端點的 body 是
       **手動**解析的(為了先認證後讀 body),FastAPI 因此**不會自動**把 schema
       寫進文件 —— 拿掉 `openapi_extra` 的話,文件上這個端點看起來不收任何 body,
       而**功能完全正常**,直到第一個來源系統照著文件寫出一個空請求。
    """
    http, _ = app_client
    op = http.get("/inbox/openapi.json").json()["paths"][PUSH]["post"]
    props = set(op["requestBody"]["content"]["application/json"]["schema"]["properties"])
    assert {"recipient_sub", "subject", "body", "action_url"} <= props, props
    assert "source_app" not in props, "🔴 文件上出現 source_app —— 它不得取自 body"
    headers = {p["name"] for p in op.get("parameters", []) if p.get("in") == "header"}
    assert {"X-User-Id", "Idempotency-Key"} <= headers, headers
    assert {"200", "201", "400", "401", "403", "422"} <= set(op["responses"]), op["responses"]


# ═══════════════════════════════════════════════════════════════════
# 12. 來源後台(Q2 答覆:新增來源 = 後台加一列;可單獨停用而不動程式)
# ═══════════════════════════════════════════════════════════════════
def test_sources_admin_requires_admin(app_client, db_session):
    """非 admin(只有 `reader`)→ 看與改都 **403,且零列**。

    ⚠ 送表單時刻意帶**正確的** CSRF token:兩者都回 403,不帶的話
      這支測試分不出擋下它的是 CSRF 還是能力判定。
    """
    http, transport = app_client
    _login(http, transport)                          # 首登只有 reader
    assert http.get(SOURCES).status_code == 403

    token = _csrf_for_current_session(http)
    r = http.post(SOURCES, data={"csrf_token": token, "azp": S2S_AZP, "label": "TRF 系統"},
                  follow_redirects=False)
    assert r.status_code == 403, f"reader 登記來源應 403,實得 {r.status_code}"
    assert r.json().get("error") == "forbidden", r.json()
    assert _count(db_session, "SourceApp") == 0, "🔴 403 卻登記了來源"


def test_admin_can_register_and_disable_a_source(app_client, db_session, idp, clock):
    """登記 → 推得進來;停用 → **立刻** 403;再啟用 → 又推得進來。

    🔴 停用必須**即時**:要等重啟才生效的話,「可停用單一來源而不動程式」(Q2)
       就是一句假話 —— 而停用最需要的時刻,正是某個來源開始亂推的時候。
    """
    http, transport = app_client
    _login(http, transport)
    _grant(db_session, "admin")
    token = _csrf_of_page(http, SOURCES)

    r = http.post(SOURCES, data={"csrf_token": token, "azp": S2S_AZP, "label": "TRF 系統"},
                  follow_redirects=False)
    assert r.status_code == 303, f"登記應 303,實得 {r.status_code} {r.text[:200]}"
    page = http.get(SOURCES).text
    assert S2S_AZP in page and "TRF 系統" in page, "登記後清單上看不到"

    push = lambda key: http.post(PUSH, json=_payload(), headers=_headers(_token(idp, clock), key=key))  # noqa: E731
    assert push("k-1").status_code == 201

    r = http.post(f"{SOURCES}/enabled", data={"csrf_token": token, "azp": S2S_AZP, "enabled": "0"},
                  follow_redirects=False)
    assert r.status_code == 303, r.text[:200]
    denied = push("k-2")
    assert denied.status_code == 403 and denied.json().get("error") == "source_disabled", (
        f"🔴 停用後仍推得進來(或錯誤碼不對):{denied.status_code} {denied.text[:200]}"
    )

    r = http.post(f"{SOURCES}/enabled", data={"csrf_token": token, "azp": S2S_AZP, "enabled": "1"},
                  follow_redirects=False)
    assert r.status_code == 303
    assert push("k-3").status_code == 201


def test_register_source_rejects_bad_values(app_client, db_session):
    """壞值 → **400 且零列**;重複登記 → 400;切換不存在的來源 → 404。

    🔴 標籤會以「寄件人」的位置顯示給**每一個收件人**,而它被複製進
       `message.source_app`(32 字)—— 超長在 PostgreSQL 上是**推送當下的 500**,
       在 SQLite 上是被無視。所以長度在登記時就擋。
    🔴 我方自己的 client_id 不能登記:它簽出的 token 一律是使用者 token(401),
       登記了也永遠推不進來,只會讓人以為設定好了。
    """
    http, transport = app_client
    _login(http, transport)
    _grant(db_session, "admin")
    token = _csrf_of_page(http, SOURCES)

    bad = {
        "空 azp": ("", "TRF 系統"),
        "空標籤": (S2S_AZP, "   "),
        "標籤超過 32 字": (S2S_AZP, "標" * 33),
        "azp 含空白": ("compliance sa", "TRF 系統"),
        "azp 超長": ("x" * 256, "TRF 系統"),
        "我方自己的 client_id": (CLIENT_ID, "站內信"),
    }
    for why, (azp, label) in bad.items():
        r = http.post(SOURCES, data={"csrf_token": token, "azp": azp, "label": label},
                      follow_redirects=False)
        assert r.status_code == 400, f"{why}:預期 400,實得 {r.status_code} {r.text[:200]}"
    assert _count(db_session, "SourceApp") == 0, "🔴 400 卻登記了"

    ok = http.post(SOURCES, data={"csrf_token": token, "azp": S2S_AZP, "label": "TRF 系統"},
                   follow_redirects=False)
    assert ok.status_code == 303
    dup = http.post(SOURCES, data={"csrf_token": token, "azp": S2S_AZP, "label": "改名"},
                    follow_redirects=False)
    assert dup.status_code == 400, f"重複登記應 400(不默默改名),實得 {dup.status_code}"
    from app.models import SourceApp

    db_session.expire_all()
    assert db_session.get(SourceApp, S2S_AZP).label == "TRF 系統", "🔴 重複登記默默改了標籤"

    missing = http.post(f"{SOURCES}/enabled",
                        data={"csrf_token": token, "azp": "nobody-sa", "enabled": "0"},
                        follow_redirects=False)
    assert missing.status_code == 404, f"切換不存在的來源應 404,實得 {missing.status_code}"


def test_sources_page_is_csp_clean_and_csrf_protected(app_client, db_session):
    """來源後台頁:CSP nonce 對得上、零行內樣式屬性、零外部資源、**每個表單都帶 CSRF**。

    ⚠ T10 的 CSP 守門與 T10b 的「頁上每個表單都有 CSRF」守門,
      都只檢查**固定清單**上的頁面(`/inbox/admin/users` 在清單上,本頁不在)——
      新頁面要自己證明一次,否則那兩條紅線在這一頁上只是假設。
    """
    http, transport = app_client
    _login(http, transport)
    _grant(db_session, "admin")
    _register(db_session)
    r = http.get(SOURCES)
    assert r.status_code == 200, r.text[:200]

    csp = r.headers.get("content-security-policy", "")
    m = re.search(r"'nonce-([A-Za-z0-9_\-]+)'", csp)
    assert m, f"CSP 沒有 nonce:{csp}"
    tags = re.findall(r"<style\b[^>]*>", r.text)
    assert tags and all(f'nonce="{m.group(1)}"' in t for t in tags), "🔴 <style> 沒帶本次的 nonce"
    stripped = re.sub(r"<style\b.*?</style>", "", r.text, flags=re.S)
    assert 'style="' not in stripped, "🔴 有行內樣式屬性,CSP 會把它擋掉"
    assert not re.search(r'(?:src|href)="https?://', r.text), "🔴 引用了外部資源"

    forms = re.findall(r"<form\b.*?</form>", r.text, re.S)
    assert len(forms) >= 2, f"預期至少兩個表單(登記 + 切換),實得 {len(forms)}"
    for form in forms:
        assert re.search(r'name="csrf_token"\s+value="[0-9a-f]{64}"', form), (
            f"🔴 有一個表單沒帶有效的 csrf_token:{form[:160]}"
        )


def test_source_label_is_escaped_on_every_page(app_client, db_session, idp, clock):
    """登記表的標籤含 `<script>` → 後台頁與收件匣頁都輸出**跳脫後**的字面。

    🔴 標籤由管理員輸入,之後以「寄件人」的身分出現在**每一個收件人**的收件匣
       —— 同源之下一次 stored XSS 可觸及 IdP(A.3 第一風險)。
    ⚠ 斷言兩件事:原始標籤不在、**跳脫後的字面在** —— 只驗前者的話,
      「乾脆不顯示標籤」也會通過,而那是功能壞掉不是安全達成。
    """
    http, transport = app_client
    _login(http, transport)
    _grant(db_session, "admin")
    _register(db_session, label="S<script>")          # 32 字內
    r = http.post(PUSH, json=_payload(), headers=_headers(_token(idp, clock)))
    assert r.status_code == 201, r.text[:200]
    for path in (SOURCES, "/inbox/"):
        html = http.get(path).text
        assert "S<script>" not in html, f"🔴 {path} 未跳脫標籤"
        assert "S&lt;script&gt;" in html, f"{path} 沒有顯示標籤(跳脫後的字面不在)"
