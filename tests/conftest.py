# -*- coding: utf-8 -*-
"""pytest 共用設定。

為什麼需要這支:測試以 `from app.main import app` 匯入應用,而 pytest 的
rootdir 不保證在 sys.path 上。這裡把 repo 根目錄插到最前面,讓測試
不論從 repo 根或 tests/ 下啟動都能匯入——CI 與本機的啟動位置本來就不同,
靠「記得 cd 對地方」是會壞的。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
