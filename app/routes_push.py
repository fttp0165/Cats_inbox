# -*- coding: utf-8 -*-
"""推送 API(T14a):`POST /inbox/api/v1/notifications`。

用途: 讓**已登記的來源系統**以服務憑證(client_credentials)把系統通知推給某個人。
副作用: 成功時 INSERT 一列 `message` + 一列 `push_receipt`(或更新過期的那一列收據);
        每一次成功與拒絕都寫一行 log(**不含主旨與內容**)。

規格權威:portal 2026-08-18 核定(契約 §11.7 第一案)+ 我方 2026-08-24 答覆 Q2/Q3。
給來源系統的操作說明:`docs/來源接入指南.md`。

判定順序(每一關都在任何寫入之前):

| # | 關卡 | 不過時 | 在哪 |
|---|---|---|---|
| 1 | 服務憑證(簽章 / `iss` / `aud` / 時間 / `typ`)| 401 | `app/s2s.py` → `OidcClient.verify_access_token` |
| 2 | scope `notification:push`(整詞)| 403 | `app/s2s.py` |
| 3 | `azp` 已登記且啟用 | 403 | `app/s2s.py` |
| 4 | body 是 JSON 物件 | 400 | 本檔 `_authenticated_json` —— 🔴 **認證之後才讀 body** |
| 5 | `X-User-Id`(UUID、不是呼叫方自己)| 400 | 本檔 |
| 6 | `Idempotency-Key`(1–128 個可見 ASCII)| 400 | 本檔 |
| 7 | `recipient_sub` 是 UUID;主旨/內容/`action_url` | 400 | 本檔 + `repo.create_message()` |
| 8 | 同 key:回放(200)/ 不同內容(422)/ 新建(201)| — | `repo.push_notification()` |

🔴 **所有輸入錯誤一律 400**(不是 FastAPI 預設的 422)—— 對齊 `docs/開發計畫書.md` §3.4 的
   錯誤語意;**422 在本端點只有一個意思:同 key 不同內容**,呼叫方看到它就知道是 key 的問題。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.exc import IntegrityError

from app.db import session_scope
from app.oidc import OidcError, log_event
from app.s2s import SCOPE_NOTIFICATION_PUSH, S2SCaller, require_s2s_scope
from app.validation import BadRequest

# Keycloak 的 `sub` 是 UUID(8-4-4-4-12,十六進位)。大小寫在 RFC 4122 是不敏感的,
# 而我方的收件匣以**小寫**字串比對 —— 所以驗形狀時不分大小寫、存的時候一律小寫。
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# `Idempotency-Key`:1–128 個**可見** ASCII 字元(0x21–0x7E,不含空白)。
# ⚠ 不做任何正規化(不去引號、不轉大小寫):key 的比對必須逐字,
#   正規化會讓兩把不同的 key 撞在一起 —— 而撞在一起的第二則會被當成重送。
_IDEMPOTENCY_KEY_RE = re.compile(r"^[\x21-\x7e]{1,128}$")

# body 上限。內容上限是 20000 字,而 ensure_ascii 的 JSON 一個中文字是 6 bytes
# (`\uXXXX`),再加上主旨與欄位名 —— 256 KiB 綽綽有餘,又能擋掉離譜的請求。
_MAX_BODY_BYTES = 256 * 1024


class PushRequest(BaseModel):
    """推送的請求 body。

    🔴 **沒有 `source_app`、`category`、`sender_sub` 欄位** —— 不是「收下來再忽略」,
       是**沒有任何一條路徑讀得到它們**(Pydantic 預設 `extra="ignore"`)。
       `source_app` 一律由呼叫方身分推導(本專案紅線:能自稱來源 = 能冒充任何系統)。
    ⚠ 刻意不用 `extra="forbid"`:那會讓 API 反過來告訴呼叫方「這個欄位名存在」。
    """

    model_config = ConfigDict(extra="ignore")

    recipient_sub: str | None = None
    subject: str = ""
    body: str = ""
    action_url: str | None = None


def _normalized_uuid(value: str | None, *, code: str, field: str) -> str:
    """驗 UUID 形狀並回傳**小寫**;不合 → BadRequest(400)。"""
    text = (value or "").strip()
    if not _UUID_RE.fullmatch(text):
        raise BadRequest(code, f"{field} 必須是 Keycloak 的 sub(UUID,8-4-4-4-12)")
    return text.lower()


def _fingerprint(*, actor: str, recipient: str, payload: PushRequest) -> str:
    """請求內容的 SHA-256(十六進位),供「同 key 不同內容」判定。

    ⚠ 觸發者(`X-User-Id`)也算內容:同一把 key 換了觸發者,就不是同一個請求的重送。
    ⚠ 用**解析後**的值而不是原始 bytes:同一份 JSON 換了欄位順序或空白,
      是同一個請求 —— 呼叫方的 JSON 函式庫不保證每次序列化長得一樣。
    """
    material = json.dumps(
        [actor, recipient, payload.subject, payload.body, payload.action_url],
        ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def build_push_router(*, settings) -> APIRouter:
    """建立推送 API 的路由。

    參數: settings — Settings 快照(取我方 client_id)
    回傳: APIRouter(呼叫方負責加 `/inbox` 前綴)
    副作用: 無(只組 router)
    """
    router = APIRouter(prefix="/api/v1", tags=["push"])
    # 🔴 **逐端點驗 scope**(§11.5):scope 是這個端點**自己宣告**的,不是全域設定。
    require_push = require_s2s_scope(
        SCOPE_NOTIFICATION_PUSH, own_client_id=settings.oidc_client_id
    )

    async def _authenticated_json(
        request: Request, caller: S2SCaller = Depends(require_push)
    ) -> tuple[S2SCaller, object]:
        """**先認證、後讀 body**,並把 body 解析成 JSON。

        回傳: (已驗證的呼叫方, 解析後的 JSON 值)
        錯誤: 認證/授權不過 → 401/403(由 `require_push` 發出,body **根本沒被讀**);
              過大或不是 JSON → 400

        🔴 為什麼不直接宣告一個 Pydantic body 參數:FastAPI 會在**解析相依之前**
           就把 body 讀進來並解析 —— 壞 JSON 的請求會在認證**之前**拿到 422。
           未認證的呼叫方因此能拿本端點探測 payload 格式,而「你沒有憑證」
           這件最重要的事被別的錯誤蓋掉了。
        """
        raw = await request.body()
        if len(raw) > _MAX_BODY_BYTES:
            raise BadRequest("payload_too_large", f"body 超過 {_MAX_BODY_BYTES} bytes")
        try:
            return caller, json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise BadRequest("invalid_json", "body 不是合法的 JSON(UTF-8)")

    @router.post(
        "/notifications",
        status_code=201,
        summary="推送一則系統通知(S2S)",
        # 🔴 `docs/開發計畫書.md` §3.4:「實作時以 OpenAPI 為權威」。body 是手動解析的
        #    (理由見 `_authenticated_json`),所以 schema 要明寫進 OpenAPI,
        #    否則 `/inbox/docs` 上這個端點看起來不收任何 body。
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": PushRequest.model_json_schema()}},
            }
        },
        responses={
            200: {"description": "24 小時內同一把 Idempotency-Key、同內容的重送:回放第一次的 id"},
            400: {"description": "輸入不合法(X-User-Id / Idempotency-Key / recipient_sub / 內容)"},
            401: {"description": "沒有或不是有效的服務憑證"},
            403: {"description": "缺 scope notification:push,或來源未登記/已停用"},
            422: {"description": "同一把 Idempotency-Key 用於內容不同的請求"},
        },
    )
    def push(
        request: Request,
        response: Response,
        ctx=Depends(_authenticated_json),
        x_user_id: str | None = Header(None, alias="X-User-Id"),
        idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    ):
        """推送一則系統通知給一個人。

        回傳: 201 `{"id": <訊息 id>}`;24h 內同 key 同內容 → **200** 同一個 id
              並帶 `Idempotent-Replayed: true`
        錯誤: 見模組檔頭的判定順序表
        副作用: 見模組檔頭
        """
        caller, data = ctx
        try:
            # ── `X-User-Id`:觸發事件的人(稽核鏈)──────────────────────
            # 🔴 portal 加嚴條件:**必帶、必驗**,不得「缺就當系統發出」——
            #    那會讓稽核鏈在最需要的時候恰好是空的。
            if not (x_user_id or "").strip():
                raise BadRequest("missing_x_user_id", "缺 X-User-Id(觸發這個事件的人的 sub)")
            actor = _normalized_uuid(x_user_id, code="invalid_x_user_id", field="X-User-Id")
            # 🔴 不得等於呼叫方自身:服務把自己的 service account 當成觸發者,
            #    同樣等於沒有稽核。
            if actor in (caller.sub.lower(), caller.azp.lower()):
                raise BadRequest("x_user_id_is_caller",
                                 "X-User-Id 不得是呼叫方自己的 service account")

            # ── `Idempotency-Key` ──────────────────────────────────────
            if not idempotency_key:
                raise BadRequest("missing_idempotency_key",
                                 "缺 Idempotency-Key(每個事件一把,重送時沿用)")
            if not _IDEMPOTENCY_KEY_RE.fullmatch(idempotency_key):
                raise BadRequest("invalid_idempotency_key",
                                 "Idempotency-Key 必須是 1–128 個可見 ASCII 字元(不含空白)")

            # ── body ──────────────────────────────────────────────────
            if not isinstance(data, dict):
                raise BadRequest("invalid_payload", "body 必須是 JSON 物件")
            try:
                payload = PushRequest.model_validate(data)
            except ValidationError as exc:
                # 🔴 只回**欄位名**,不回原值(內容不回彈,與不進 log 同一個理由)
                fields = sorted({str(e["loc"][0]) for e in exc.errors() if e.get("loc")})
                raise BadRequest("invalid_payload", f"欄位型別不對:{', '.join(fields)}")
            # 🔴 收件匣以 `sub` 比對收件人。傳 email 或帳號名進來的話會**回 201**,
            #    而那則訊息**沒有任何人看得到** —— 所以形狀不對一律 400。
            recipient = _normalized_uuid(payload.recipient_sub, code="invalid_recipient_sub",
                                         field="recipient_sub")

            fingerprint = _fingerprint(actor=actor, recipient=recipient, payload=payload)
            now = datetime.fromtimestamp(request.app.state.clock(), timezone.utc)
            args = dict(caller=caller, key=idempotency_key, fingerprint=fingerprint,
                        actor=actor, recipient=recipient, payload=payload, now=now)
            try:
                message_id, replayed = _push_once(**args)
            except IntegrityError:
                # 🔴 並行的同 key 請求在我們「查」與「寫」之間先寫入了:
                #    我們的交易已整個回滾(訊息也沒留下),再走一次就會查到它 → 回放。
                #    回 500 的話,呼叫方會重試而成功 —— 但那個 500 已經讓對方的告警響了。
                message_id, replayed = _push_once(**args)
        except OidcError as exc:
            # 400 / 422:留一行拒絕紀錄(🔴 只記代碼與身分,**不記內容**)
            log_event("push_rejected", reason=exc.code, azp=caller.azp)
            raise

        # 🔴 log 只記 id、sub、事件類型(A.3:主旨與內容不進 log)
        log_event(
            "notification_pushed",
            azp=caller.azp,
            actor_sub=actor,
            recipient_sub=recipient,
            message_id=message_id,
            replayed=replayed,
        )
        if replayed:
            response.status_code = 200
            response.headers["Idempotent-Replayed"] = "true"
        return {"id": message_id}

    return router


def _push_once(*, caller: S2SCaller, key: str, fingerprint: str, actor: str,
               recipient: str, payload: PushRequest, now: datetime) -> tuple[str, bool]:
    """在一個交易裡跑一次 `repo.push_notification`。

    回傳: (message_id 字串, 是否為回放)
    副作用: 見 `repo.push_notification`
    錯誤: 並行寫入撞唯一約束 → `IntegrityError`(交易已回滾)
    """
    from app.repo import push_notification

    with session_scope() as db:
        message_id, replayed = push_notification(
            db,
            azp=caller.azp,
            source_app=caller.source_app,      # 🔴 來自登記表,不是 body
            idempotency_key=key,
            request_hash=fingerprint,
            actor_sub=actor,
            recipient_sub=recipient,
            subject=payload.subject,
            body=payload.body,
            action_url=payload.action_url,
            now=now,
        )
        return str(message_id), replayed
