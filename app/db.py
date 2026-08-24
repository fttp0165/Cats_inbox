# -*- coding: utf-8 -*-
"""資料庫連線與 session 管理。

用途: 提供一個行程內共用的 engine 與短生命週期的 session。
副作用: 建立連線池;不建表(建表一律走 Alembic migration)。

🔴 **不在應用啟動時 `create_all()`。** 理由:那會讓「schema 怎麼來的」有兩個
   來源(migration 與程式),而它們遲早不一致——而不一致的症狀是
   「本機好好的,部署後少一個欄位」。schema 的唯一權威是 `alembic/versions/`。
   (測試是例外:測試用 SQLite 現造 schema,見 `tests/conftest.py` 的說明。)
"""

from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

_engine = None
_Session: sessionmaker | None = None


def init_engine(url: str, **kwargs):
    """建立(或重建)engine。

    參數: url — SQLAlchemy 連線字串;kwargs — 傳給 `create_engine`
    回傳: Engine
    副作用: 取代模組層的 engine 與 sessionmaker

    可重複呼叫是刻意的:測試要在同一個行程裡換到別的資料庫。
    """
    global _engine, _Session
    _engine = create_engine(url, future=True, **kwargs)
    _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_engine():
    """取得 engine;未初始化即明確失敗(不隱式連到別的地方)。"""
    if _engine is None:
        raise RuntimeError("engine 未初始化——請先呼叫 init_engine()")
    return _engine


@contextmanager
def session_scope() -> Session:
    """一個交易範圍的 session。

    回傳: Session(context manager)
    副作用: 正常結束 commit、拋錯 rollback、一律 close

    🔴 刻意用 context manager 而不是把 session 掛在全域:
    長壽命 session 會把過期的物件留在身分判定路徑上,
    而那正是契約 §4.2a 對快取最擔心的事(拿舊資料做判斷)。
    """
    if _Session is None:
        raise RuntimeError("sessionmaker 未初始化——請先呼叫 init_engine()")
    session = _Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
