# -*- coding: utf-8 -*-
"""Alembic 執行環境。

用途: 讓 `alembic upgrade/downgrade` 拿到連線字串與 model metadata。
副作用: 連資料庫並執行 DDL。

🔴 連線字串一律自 **env** 讀,不寫進 `alembic.ini`:
   ① secret 不進 git(共通紅線);
   ② 避免 `alembic.ini` 與 `.env` 出現兩個真相——而不一致的症狀是
      「migration 跑在另一個資料庫上」,那不會報錯,只會讓表出現在錯的地方。
"""

import os
import sys
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import Base  # noqa: E402

config = context.config

# 測試用 INBOX_TEST_DB_URL 覆寫,正式用 INBOX_DB_URL。
# 兩個都沒有時明確失敗,不預設連到 localhost —— 猜錯的資料庫比連不上更難查。
url = os.getenv("INBOX_TEST_DB_URL") or os.getenv("INBOX_DB_URL")
if not url:
    raise SystemExit("❌ 未設定 INBOX_DB_URL(或測試用 INBOX_TEST_DB_URL)")
config.set_main_option("sqlalchemy.url", url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """離線模式:產生 SQL 而不連線。"""
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """線上模式:實際連線執行 DDL。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
