# -*- coding: utf-8 -*-
"""T07 schema 紅測試(三張表 + migration `0002`)。

對應驗收:`docs/任務表.md` T07、上游《站內信通知中心設計規劃》**§4**、
本專案紅線(業務庫只存 `sub`、body 純文字)。

🔴 本檔最有價值的一支是 `test_models_match_migration_schema` ——
   它補的是 **T05 誠實標註的盲點**:應用層測試的 schema 來自 `create_all()`,
   而正式環境的來自 migration,**在此之前沒有任何一支測試在比較兩者**。
   兩者漂移的症狀是「本機好好的,部署後少一個欄位」。

⚠ 本檔全部需要真的 PostgreSQL;無可用 PG 時 **skip 而非 pass**
   (「沒跑」不得與「跑過且綠」長得一樣)。
   ⚠ 仍是 **PG16** 演練;PG15 依 T05 的遺留留在 T11。
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("INBOX_TEST_DB_URL"),
    reason="需要真的 PostgreSQL:設 INBOX_TEST_DB_URL 後才跑(見 tests/pg_local.sh)",
)

T07_TABLES = ("message", "announcement", "announcement_read")


def _insp(engine):
    from sqlalchemy import inspect

    return inspect(engine)


# ═══════════════════════════════════════════════════════════════════
# 1. migration 雙向
# ═══════════════════════════════════════════════════════════════════
def test_migration_0002_up_down_up(alembic_cfg, pg_engine):
    """`0002` 建三張表;降到 `0001` 時三張表消失而 `0001` 的兩張**留著**。

    🔴 後半是重點:降一級不得把 T05 的表一起帶走。
    Alembic 的 `downgrade -1` 只跑一個 revision,但如果 `0002` 的
    `downgrade()` 手誤 drop 了 `app_user`,測試不看就不會發現——
    而那在正式環境是**把所有人的身分與角色刪掉**。
    """
    from alembic import command

    command.upgrade(alembic_cfg, "head")
    tables = set(_insp(pg_engine).get_table_names())
    for t in T07_TABLES:
        assert t in tables, f"upgrade 後缺 {t},實得 {sorted(tables)}"

    command.downgrade(alembic_cfg, "0001")
    tables = set(_insp(pg_engine).get_table_names())
    for t in T07_TABLES:
        assert t not in tables, f"🔴 downgrade 後 {t} 還在——backward 是假的"
    assert {"app_user", "user_role"} <= tables, (
        f"🔴 降到 0001 卻把 T05 的表也刪了——那在正式環境是刪掉所有人的身分。"
        f"實得 {sorted(tables)}"
    )

    command.upgrade(alembic_cfg, "head")
    assert set(T07_TABLES) <= set(_insp(pg_engine).get_table_names())


# ═══════════════════════════════════════════════════════════════════
# 2. 欄位:上游 §4.1 齊備 + 紅線欄位不存在
# ═══════════════════════════════════════════════════════════════════
def test_message_schema_matches_upstream(alembic_cfg, pg_engine):
    """`message` 的欄位與上游 §4.1 一致,且**沒有** email / 姓名 / 密碼欄。

    上游 §4 開頭寫「四張表皆遵平台憲章:**只存 sub,不存 email/姓名**」。
    schema 層斷言比行為層強——欄位不存在就是寫不進去。
    """
    from alembic import command

    command.upgrade(alembic_cfg, "head")
    cols = {c["name"]: c for c in _insp(pg_engine).get_columns("message")}

    upstream = {
        "id", "recipient_sub", "sender_sub", "category", "subject", "body",
        "action_url", "source_app", "thread_id", "is_read", "read_at", "created_at",
    }
    missing = upstream - set(cols)
    assert not missing, f"缺上游 §4.1 的欄位:{sorted(missing)}"

    forbidden = {"email", "password", "password_hash", "mail",
                 "sender_name", "recipient_name", "full_name"}
    assert not (set(cols) & forbidden), (
        f"🔴 有不該存在的欄位:{sorted(set(cols) & forbidden)}"
    )

    # body 必須是不限長度的 TEXT:純文字通知可能很長,而 VARCHAR(n) 的
    # 截斷在 PG 上是**直接報錯**,症狀是「某些通知推不進來」。
    assert cols["body"]["type"].__class__.__name__.upper() in ("TEXT", "TEXT_"), (
        f"body 應為 TEXT,實得 {cols['body']['type']}"
    )


def test_announcement_schema_matches_upstream(alembic_cfg, pg_engine):
    """`announcement` 的欄位與上游 §4.2 一致(含刻意沿用的 `title` 用詞)。"""
    from alembic import command

    command.upgrade(alembic_cfg, "head")
    cols = {c["name"] for c in _insp(pg_engine).get_columns("announcement")}
    upstream = {"id", "author_sub", "title", "body", "audience",
                "starts_at", "ends_at", "created_at"}
    assert upstream <= cols, f"缺上游 §4.2 的欄位:{sorted(upstream - cols)}"
    # 🔴 `title` 而非 `subject`:上游 §4.2 就是這個字。改名會讓上游 §2.1 的
    #    「退路=搬四張表」失效(變成一次資料遷移)。
    assert "subject" not in cols, "改成 subject 了——上游 §4.2 用的是 title"


# ═══════════════════════════════════════════════════════════════════
# 3. 🔴 兩張表對「sub 要不要設外鍵」給出相反的答案,兩邊都要釘住
# ═══════════════════════════════════════════════════════════════════
def test_message_recipient_sub_has_no_fk(alembic_cfg, pg_engine):
    """🔴 `message.recipient_sub` **不得**有外鍵到 `app_user`。

    推送 API(T14)必須能推給**從未登入過 inbox 的人**——新到職的同事,
    或 PLM 那 50 位沒有 email 的測試工程師。設了外鍵,那些推送會被資料庫擋掉,
    而症狀是「通知沒送到」——**收件人自己不會知道有東西被丟掉**。

    這條斷言擋的是「日後有人順手把外鍵補上」:那個動作看起來是在修
    一個缺漏的約束,實際上是把一個能力靜默關掉。
    """
    from alembic import command

    command.upgrade(alembic_cfg, "head")
    fks = _insp(pg_engine).get_foreign_keys("message")
    offending = [fk for fk in fks if "recipient_sub" in (fk.get("constrained_columns") or [])]
    assert not offending, (
        f"🔴 `recipient_sub` 被加上外鍵了:{offending}。"
        "那會讓推給「還沒登入過的人」的通知被資料庫擋掉,而收件人不會知道"
    )


def test_announcement_read_has_fks_and_cascades(alembic_cfg, pg_engine):
    """`announcement_read` 兩個外鍵都要有,且公告刪除時已讀紀錄跟著走。

    ⚠ 與上一條**相反**是刻意的:要標記公告已讀,你必然已經登入過,
    所以 `app_user` 那一列必定存在。理由也寫在 migration 與 model 的註釋裡
    ——不寫的話,這兩條在日後看起來會像不一致。
    """
    from alembic import command

    command.upgrade(alembic_cfg, "head")
    fks = _insp(pg_engine).get_foreign_keys("announcement_read")
    targets = {fk["referred_table"]: fk for fk in fks}
    assert "announcement" in targets, f"缺到 announcement 的外鍵,實得 {fks}"
    assert "app_user" in targets, f"缺到 app_user 的外鍵,實得 {fks}"
    for name, fk in targets.items():
        assert (fk.get("options") or {}).get("ondelete", "").upper() == "CASCADE", (
            f"到 {name} 的外鍵不是 CASCADE:{fk}"
        )


def test_announcement_read_unique_per_person(alembic_cfg, pg_engine):
    """同一個人對同一則公告只能有一列(公告**不逐人複製**)。

    上游 §4.2:「AnnouncementRead 記錄誰讀過,公告不逐人複製」。
    沒有唯一約束的話,重複標已讀會長出重複列,而**已讀在畫面上仍然正常**
    ——只有列數在悄悄長。
    """
    from alembic import command

    command.upgrade(alembic_cfg, "head")
    uniques = _insp(pg_engine).get_unique_constraints("announcement_read")
    combos = [set(u["column_names"]) for u in uniques]
    assert {"announcement_id", "user_sub"} in combos, (
        f"缺 (announcement_id, user_sub) 的唯一約束,實得 {uniques}"
    )


# ═══════════════════════════════════════════════════════════════════
# 4. 未讀鈴鐺的索引(30 秒輪詢)
# ═══════════════════════════════════════════════════════════════════
def test_unread_count_index_exists(alembic_cfg, pg_engine):
    """未讀數查詢要有覆蓋 `(recipient_sub, is_read)` 的索引。

    A.1 明訂未讀鈴鐺用 **30 秒輪詢**——這個查詢會由每一個開著入口首頁的人
    每 30 秒跑一次。沒有索引時它是全表掃描,而**在資料少的時候完全看不出來**;
    症狀要等到訊息累積起來才出現,而且是「整個入口變慢」而不是「inbox 變慢」。
    """
    from alembic import command

    command.upgrade(alembic_cfg, "head")
    indexes = _insp(pg_engine).get_indexes("message")
    ok = any(
        set(ix["column_names"][:2]) == {"recipient_sub", "is_read"}
        or (ix["column_names"] and ix["column_names"][0] == "recipient_sub"
            and "is_read" in ix["column_names"])
        for ix in indexes
    )
    assert ok, f"缺 (recipient_sub, is_read) 的索引,實得 {indexes}"


# ═══════════════════════════════════════════════════════════════════
# 5. CHECK 約束真的擋得住
# ═══════════════════════════════════════════════════════════════════
def test_category_check_rejects_unknown_value(alembic_cfg, pg_engine):
    """`category` 只接受 `system` / `direct`,其餘由**資料庫**擋掉。

    做法是真的 INSERT 一筆壞資料看它被拒——不是讀約束定義。
    讀定義只能證明「約束存在」,INSERT 才能證明「它會擋」。
    """
    from alembic import command
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    command.upgrade(alembic_cfg, "head")
    ins = text(
        "INSERT INTO message (id, recipient_sub, category, subject, body, created_at) "
        "VALUES (gen_random_uuid(), 'someone', :cat, 's', 'b', now())"
    )
    with pg_engine.begin() as conn:
        conn.execute(ins, {"cat": "system"})     # 合法值必須成功
    with pytest.raises(IntegrityError):
        with pg_engine.begin() as conn:
            conn.execute(ins, {"cat": "not-a-category"})


def test_audience_check_allows_only_all_for_now(alembic_cfg, pg_engine):
    """🔴 `audience` 現階段只接受 `all`。

    上游 §4.2 寫「`all` / 群組」,但**群組定向我方做不到**:
    它需要 `groups` claim,而我方 client **刻意沒有申請 groups**
    (T03 申請書〈貳〉明載不申請 `email`、不申請 `groups`)。

    欄位照上游建,但約束把它擋在 `all` —— 因為
    **建了欄位卻假裝能用**比不建更糟:發布端會以為自己設定了收件範圍,
    而實際上每一則都送給所有人。要開放群組定向須先向 portal 申請 §4.2a L2。
    """
    from alembic import command
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    command.upgrade(alembic_cfg, "head")
    ins = text(
        "INSERT INTO announcement (id, author_sub, title, body, audience, starts_at, created_at) "
        "VALUES (gen_random_uuid(), 'admin-sub', 't', 'b', :aud, now(), now())"
    )
    with pg_engine.begin() as conn:
        conn.execute(ins, {"aud": "all"})
    with pytest.raises(IntegrityError):
        with pg_engine.begin() as conn:
            conn.execute(ins, {"aud": "group:rd"})


# ═══════════════════════════════════════════════════════════════════
# 6. 🔴 補 T05 留下的盲點:model 與 migration 的 schema 必須一致
# ═══════════════════════════════════════════════════════════════════
def test_models_match_migration_schema(alembic_cfg, pg_engine, models_engine):
    """`create_all()` 產出的 schema 必須與 **migration** 產出的一致。

    🔴 這支補的是 T05 誠實標註的盲點:應用層測試用 `create_all()` 建 schema,
    而正式環境用 migration —— 在此之前**沒有任何一支測試在比較兩者**。
    漂移的症狀是「本機測試全綠,部署後少一個欄位」,而那要等到
    某個查詢真的用到它才會出現。

    比對:表名集合、每張表的欄位名集合、以及欄位的 nullable。
    ⚠ **刻意不比對型別字串**:SQLAlchemy 反射回來的型別在 `create_all` 與
    手寫 DDL 之間可能有等價但不同名的表示(例:`VARCHAR` vs `VARCHAR(n)`),
    那會產生假紅燈。抓「欄位少了 / 多了 / nullable 反了」已經涵蓋
    絕大多數真正會出事的漂移。
    """
    from alembic import command
    from sqlalchemy import inspect

    from app.models import Base

    command.upgrade(alembic_cfg, "head")
    Base.metadata.create_all(models_engine)

    mig, mod = inspect(pg_engine), inspect(models_engine)

    mig_tables = {t for t in mig.get_table_names() if t != "alembic_version"}
    mod_tables = set(mod.get_table_names())
    assert mig_tables == mod_tables, (
        f"表名不一致\n  只在 migration:{sorted(mig_tables - mod_tables)}"
        f"\n  只在 model:{sorted(mod_tables - mig_tables)}"
    )

    for table in sorted(mig_tables):
        a = {c["name"]: c["nullable"] for c in mig.get_columns(table)}
        b = {c["name"]: c["nullable"] for c in mod.get_columns(table)}
        assert set(a) == set(b), (
            f"`{table}` 欄位不一致\n  只在 migration:{sorted(set(a) - set(b))}"
            f"\n  只在 model:{sorted(set(b) - set(a))}"
        )
        differing = {k: (a[k], b[k]) for k in a if a[k] != b[k]}
        assert not differing, f"`{table}` 的 nullable 不一致(migration, model):{differing}"
