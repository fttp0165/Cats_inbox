# -*- coding: utf-8 -*-
"""寫入端的輸入驗證,以及它會丟出的兩個客戶端錯誤。

用途: 把「使用者送進來的值合不合法」集中成幾個小函式,讓每一種拒收
      都是**同一種形狀的 400**,而不是各端點各自發明一種回應。
副作用: 無(純判定,不查 DB、不寫任何東西)。

🔴 **為什麼是 400 而不是讓它掉進資料庫:**
   `title` 超過 255 字在 PostgreSQL 上是錯誤、在 SQLite 上被無視 ——
   也就是**本機測試綠、上線 500**。`audience` 不是 `all` 時撞到 CHECK
   同樣是 500,而呼叫方讀到的是「伺服器壞了」不是「這個值不支援」。
   擋在這裡,兩種情境才會回同一個明確的答案。

⚠ `Forbidden`(403)**不在本檔**,它在 `app/authz.py` —— 那是授權判定的結果,
   與「你送的值不合法」是兩件事;放在一起遲早會被合併成同一個回傳值,
   而 401/403/400 三者混用會讓查問題的人分不出該改什麼。
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.oidc import OidcError


class BadRequest(OidcError):
    """輸入不合法 → **400**。

    ⚠ 訊息只說「哪一個欄位、哪一種不合法」,**不回傳原值** ——
    公告的標題與內容不進 log 也不該原封回彈(本專案紅線的同一個理由)。
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(400, code, detail)


class NotFound(OidcError):
    """找不到那個東西 → **404**。

    ⚠ 什麼時候該回 404、什麼時候該回 403,取決於**有沒有歸屬要保護**:
      - 訊息有(「這是不是你的」)→ 回 403,不洩漏「這個 id 存在」;
      - 公告沒有(對所有人可見)→ 回 404,因為 403 會讓人以為是自己權限不足。
    這兩個決定寫在各自的端點註釋裡,不要憑「哪個看起來比較安全」選。
    """

    def __init__(self, detail: str) -> None:
        super().__init__(404, "not_found", detail)


def require_text(value: str | None, *, field: str, max_length: int) -> str:
    """必填文字欄位:去頭尾空白後不得為空,且不得超長。

    參數: value;field — 欄位名(只用於錯誤訊息);max_length — 對應 DB 欄寬
    回傳: 去過空白的字串
    錯誤: 空白或超長 → BadRequest(400)

    🔴 「只有空白字元」也算空。不擋的話會生出一則標題是三個空格的公告,
       它在列表上是一條看不出來的空白 —— 沒有人會覺得那是錯誤。
    """
    text = (value or "").strip()
    if not text:
        raise BadRequest("empty_field", f"{field} 不得為空")
    if len(text) > max_length:
        raise BadRequest("field_too_long", f"{field} 超過 {max_length} 字")
    return text


def require_choice(value: str | None, *, field: str, allowed: tuple[str, ...]) -> str:
    """列舉欄位:只接受白名單內的值。

    錯誤: 不在白名單 → BadRequest(400)

    🔴 白名單而不是黑名單。`audience` 的「群組」我方**做不到**
       (需要 `groups` claim,而本專案的 client 刻意沒有申請),
       而**建了欄位卻假裝能用比不建更糟**:發布者以為自己設定了收件範圍,
       實際上每一則都送給所有人(見 `app/models.py` 的 `AUDIENCES`)。
    """
    if value not in allowed:
        raise BadRequest("unsupported_value", f"{field} 只接受:{', '.join(allowed)}")
    return value


def parse_aware_datetime(value: str, *, field: str) -> datetime:
    """解析 ISO 8601 時間,**必須帶時區**,回傳 UTC。

    參數: value — 例 `2026-08-30T02:00:00+08:00`;field — 欄位名
    回傳: 帶 UTC 時區的 datetime
    錯誤: 格式錯或**不帶時區** → BadRequest(400)

    🔴 **不帶時區一律拒收,不預設當成 UTC。**
       台灣同事輸入 `2026-08-30T02:00:00` 想的是 UTC+8;當成 UTC 存進去
       就差 8 小時 —— 公告晚 8 小時才出現,而**沒有任何錯誤訊息**。
       這是 portal 憲法第四條7 記載的同一種坑(平台的 log 是 UTC、
       文件寫 UTC+8,標錯會把同一秒的兩筆事件讀成不相干)。
       ⚠ 「預設當成 UTC+8」同樣不行:那會讓 API 只對台灣正確,
       而錯的那一次一樣沒有錯誤訊息。要求呼叫方明講是唯一不會猜錯的做法。
    """
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise BadRequest("invalid_datetime", f"{field} 不是合法的 ISO 8601 時間")
    if parsed.tzinfo is None:
        raise BadRequest("naive_datetime", f"{field} 必須帶時區(例:+08:00 或 Z)")
    return parsed.astimezone(timezone.utc)


def iso_utc(value: datetime | None) -> str | None:
    """把儲存的時間輸出成**帶 UTC 位移**的 ISO 8601 字串。

    參數: value — 資料庫取回的 datetime(可為 None)
    回傳: 例 `2026-08-30T02:00:00+00:00`;value 為 None 時回 None
    副作用: 無

    🔴 **輸出必須與輸入對稱。** 本檔的 `parse_aware_datetime` 拒收不帶時區的
       輸入,理由是「差 8 小時而沒有錯誤訊息」;那麼吐出不帶時區的輸出
       就是把同一個坑挖給呼叫方。

    ⚠ **為什麼這裡可以把 naive 當成 UTC,而輸入時不行**(看起來矛盾,不是):
       - 輸入的值來自人或別的系統,他想的可能是任何時區,**我方無從得知**;
       - 輸出的值來自我方自己的儲存,而寫入端一律是 `_utcnow()`(帶 UTC)、
         或經 `parse_aware_datetime` 正規化成 UTC —— 慣例是我方自己保證的。
       naive 只會在 **SQLite** 上出現(它不存時區;PostgreSQL 的
       `timestamptz` 會保留)。⚠ 這正是 `tests/conftest.py` 記載的那個盲點:
       應用層測試跑在 SQLite 上,而正式環境是 PG —— 兩者的行為在這裡真的不同,
       症狀是同一個欄位在「剛寫入」與「重讀」時字串長得不一樣。
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc).isoformat()
    return value.astimezone(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════════════
# T10:`action_url` 同站白名單
# ═══════════════════════════════════════════════════════════════════════

# 🔴 **寫死,不從設定讀。** 兩個理由:
#    ① 平台在 D2″ 之下**只有一個 hostname**(MIS 只核可一個),
#       所以這裡沒有東西需要「設定」——多一個設定項只多一個可以被改錯的地方;
#    ② 設定檔**可以被改寬,而改寬不會有任何症狀**(釣魚連結長得跟正常連結一樣)。
#       與 `app/authz.py` 的 `ROLE_CAPABILITIES` 是同一個理由。
#    要改站台就改這一行,而它有測試守著。
SITE_ORIGIN = "https://catsapp.sporton.com.tw"
ACTION_URL_MAX = 512   # 對應 `Message.action_url` 的欄寬


def validate_action_url(value: str | None, *, field: str = "action_url") -> str | None:
    """驗 `action_url` 是**同站**值,否則 400 拒收。

    參數: value — 待驗的值(None / 空白 = 沒有,合法)
    回傳: 通過的原值(去過頭尾空白),或 None
    錯誤: 不合白名單 → BadRequest(400)
    副作用: 無

    **接受兩種形狀,其餘一律拒收:**
      1. `/` 開頭的相對路徑(但第二個字元是 `/` 或 `\\` 的除外,見下);
      2. `https://catsapp.sporton.com.tw` 之後**緊接 `/` 或字串結束**。

    🔴 **站內通知是現成的釣魚載具** —— 它天生長得「可信」(來自公司系統、
       在公司的網域裡、旁邊是一顆「前往處理」按鈕)。

    🔴 **三個看起來會過的洞,逐一擋掉:**

    | 反例 | 為什麼會過 | 怎麼擋 |
    |---|---|---|
    | `//evil.tld/x` | 是 `/` 開頭 | 第二個字元是 `/` 就拒 |
    | `/\\evil.tld/x` | 是 `/` 開頭,而**瀏覽器把 `/\\` 正規化成 `//`** | 第二個字元是 `\\` 也拒 |
    | `https://catsapp.sporton.com.tw.evil.tld/` | 通過 `startswith(站台)` **而網域是別人的** | 前綴之後必須是 `/` 或結束 |

    同一道「前綴之後必須是 `/` 或結束」也擋掉 `...com.tw@evil.tld/`(userinfo 冒充)。

    ⚠ scheme 與 host **大小寫不敏感**(URL 規範),所以比對前綴時轉小寫
      —— 但**只轉前綴那一段**:整條轉小寫會把路徑也改掉,而路徑是大小寫敏感的。

    ⚠ 控制字元一律拒收:**瀏覽器會把 URL 裡的換行/定位字元剝掉**,
      所以 `java\\nscript:` 這種寫法在別的比對方式下會變成可執行的協定。
    """
    if value is None:
        return None
    url = value.strip()
    if not url:
        return None
    if len(url) > ACTION_URL_MAX:
        raise BadRequest("action_url_too_long", f"{field} 超過 {ACTION_URL_MAX} 字")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        raise BadRequest("action_url_control_char", f"{field} 含控制字元")

    if url.startswith("/"):
        if url[1:2] in ("/", "\\"):
            # 協定相對:`//evil.tld` 與(經瀏覽器正規化後的)`/\evil.tld`
            raise BadRequest("action_url_protocol_relative", f"{field} 不得為協定相對網址")
        return url

    if url[: len(SITE_ORIGIN)].lower() == SITE_ORIGIN:
        rest = url[len(SITE_ORIGIN):]
        if rest == "" or rest.startswith("/"):
            return url

    raise BadRequest(
        "action_url_not_same_site",
        f"{field} 只接受 / 開頭的相對路徑或 {SITE_ORIGIN}/ 前綴",
    )
