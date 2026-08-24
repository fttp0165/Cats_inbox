# -*- coding: utf-8 -*-
"""T05 migration 紅測試:up → down → up 在**真的 PostgreSQL** 上演練。

🔴 為什麼一定要真的 PostgreSQL,不能拿 SQLite 代替:
   本專案的平台紅線是 PostgreSQL 15,而 SQLite 對 DDL 的接受度寬得多
   (它幾乎不檢查型別、`ALTER TABLE` 語意也不同)。在 SQLite 上綠的
   migration 到 PG 上可能直接失敗,而那會**發生在部署當下**。

⚠ **誠實標註:本機只有 PostgreSQL 16.13**(無 PG15、無 docker daemon)。
   PG16 演練**不等於** PG15 演練。本檔驗的是「DDL 在真的 PostgreSQL 上
   雙向跑得動」;**PG15 的演練列為 T11 於 VM 上補**,不得因本檔全綠
   就宣稱已符合 PG15 紅線。

⚠ 無可用 PostgreSQL 時本檔 **skip 而非 pass** ——「沒跑」不得與「跑過且綠」
   長得一樣(沿用 `tests/run_all.sh` 對 pytest 缺失的同一個原則)。
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("INBOX_TEST_DB_URL"),
    reason="需要真的 PostgreSQL:設 INBOX_TEST_DB_URL 後才跑(見 tests/pg_local.sh)",
)


def _tables(engine) -> set[str]:
    from sqlalchemy import inspect

    return set(inspect(engine).get_table_names())


def test_migration_up_down_up(alembic_cfg, pg_engine):
    """up → down → up 三段都必須成功,且 down 真的把表刪掉。

    🔴 `downgrade` 寫成 `pass` 也會讓 up→down→up「通過」——所以這裡
    斷言的是**表在 down 之後真的不見了**,不是「指令沒報錯」。
    共通紅線:動資料的 migration 必須提供 backward(可回滾)。
    """
    from alembic import command

    command.upgrade(alembic_cfg, "head")
    after_up = _tables(pg_engine)
    assert "app_user" in after_up, f"upgrade 後沒有 app_user,實得 {sorted(after_up)}"
    assert "user_role" in after_up

    command.downgrade(alembic_cfg, "base")
    after_down = _tables(pg_engine)
    assert "app_user" not in after_down, (
        f"🔴 downgrade 後 app_user 還在——backward 是假的。實得 {sorted(after_down)}"
    )
    assert "user_role" not in after_down

    command.upgrade(alembic_cfg, "head")
    assert "app_user" in _tables(pg_engine), "第二次 upgrade 失敗(migration 不可重入)"


def test_schema_has_no_identity_columns(alembic_cfg, pg_engine):
    """schema 層再確認一次:沒有 email / 密碼 / 姓名欄。

    與 `test_authz.py` 那條的差別:那條驗的是**跑完應用之後**的 schema,
    這條驗的是**migration 本身**產出的 schema。兩者可能不同——
    有人在應用層 `create_all()` 補了欄位,而 migration 沒有。
    """
    from alembic import command
    from sqlalchemy import inspect

    command.upgrade(alembic_cfg, "head")
    cols = {c["name"] for c in inspect(pg_engine).get_columns("app_user")}
    forbidden = {"email", "password", "password_hash", "mail", "name", "full_name"}
    assert not (cols & forbidden), f"🔴 migration 建出了不該有的欄位:{sorted(cols & forbidden)}"
    assert {"sub", "display_name", "is_active"} <= cols, f"缺必要欄位,實得 {sorted(cols)}"


def test_downgrade_is_not_a_stub():
    """`downgrade()` 不得是空的(源碼層檢查)。

    ⚠ 上面那條測試已經驗了「表真的不見了」,但它只驗**最新那一個** revision。
    這條掃**每一個** version 檔——日後新增 migration 時,寫了
    `def downgrade(): pass` 會在這裡就紅,不用等到有人真的要回滾。
    """
    import ast

    from tests.conftest import ROOT

    # ⚠ 同上:不用相對路徑,否則 `run_all.sh`(cwd=tests/)與從 repo 根跑
    #   會得到不同的結果。
    versions = sorted((ROOT / "alembic/versions").glob("*.py"))
    assert versions, "alembic/versions 底下沒有任何 migration"
    for path in versions:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        fn = next(
            (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "downgrade"),
            None,
        )
        assert fn is not None, f"{path.name} 沒有 downgrade()"
        # ⚠ 只去掉**開頭的 docstring**,不是所有 `ast.Expr`。
        #   `op.drop_table(...)` 本身就是一個 expression statement,
        #   濾掉全部 Expr 會讓每一個**正確的** downgrade 都被判成空的
        #   ——這支測試第一次跑就是這樣紅的,而紅的原因在測試自己身上。
        body = list(fn.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            body = body[1:]
        assert body and not all(isinstance(s, ast.Pass) for s in body), (
            f"🔴 {path.name} 的 downgrade() 是空的——backward 是假的"
        )
