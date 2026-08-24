#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清除 `display_name` L1 快取的 CLI 工具(契約 §4.2a L1 第 7 條)。

用途: 讓維運不必登入後台也能清除顯示名稱快取(單筆或整批)。
副作用: **UPDATE `app_user`**,把 `display_name` 與其時間戳設為 NULL。
        使用者本身與角色一律不動——清的只有**副本**,真相來源是 Keycloak。

用法(在容器內或設好 `INBOX_DB_URL` 的環境):
    python3 tools/purge_display_names.py --all
    python3 tools/purge_display_names.py --sub <sub>
    python3 tools/purge_display_names.py --all --dry-run

🔴 為什麼要有這支,而不是只在後台放一個按鈕:
   後台按鈕需要一個**還能登入的 admin**。而「整批清除」最需要被執行的時刻
   之一,正是「這份快取不該再存在」——那時候可能沒有人有 admin 角色。
   一個只能從 UI 觸發的清除工具,在最需要它的情境下剛好不可用。

⚠ 本工具**一律印出實際清了幾列**。一個永遠成功卻什麼都沒清的工具
   比沒有工具更糟——它讓人以為已經清了。
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    """解析參數並執行清除。

    回傳: 0 成功;2 參數錯誤;1 缺設定
    副作用: 連資料庫;非 --dry-run 時 commit
    """
    ap = argparse.ArgumentParser(description="清除 display_name L1 快取")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="整批清除")
    group.add_argument("--sub", help="只清這一個 sub")
    ap.add_argument("--dry-run", action="store_true", help="只報數量,不寫入")
    args = ap.parse_args()

    url = os.getenv("INBOX_DB_URL")
    if not url:
        print("❌ 未設定 INBOX_DB_URL", file=sys.stderr)
        return 1

    from app.db import init_engine, session_scope
    from app.repo import purge_display_names

    init_engine(url)
    with session_scope() as db:
        n = purge_display_names(db, sub=args.sub)
        if args.dry_run:
            # rollback 由 session_scope 的例外路徑負責;這裡用明確的方式退出
            db.rollback()
            print(f"[dry-run] 會清除 {n} 列(未寫入)")
            return 0
    scope = args.sub if args.sub else "全部"
    print(f"✅ 已清除 {n} 列(範圍:{scope})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
