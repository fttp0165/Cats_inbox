# -*- coding: utf-8 -*-
"""T05 首登建號、自動授 reader、deny-by-default 紅測試。

對應驗收:`docs/任務表.md` T05、《帳號系統接入契約》§4.2/§4.3/§4.4、
以及 portal 2026-08-18 核可 DEC-16 所附的**四條件**(同列本專案紅線)。

🔴 這一組測試裡最重要的不是「reader 能做什麼」,是**「reader 不能做什麼」**。
   核可條件 C3 明文要求以**反向測試**釘住範圍——因為一個過寬的角色
   在功能上完全正常,**沒有任何症狀**;它只在有人拿它做了不該做的事時才顯現,
   而那時已經發生了。
"""

from __future__ import annotations

import pytest

from tests.conftest import _login


# ═══════════════════════════════════════════════════════════════════
# 1. 首登建號:只存 sub
# ═══════════════════════════════════════════════════════════════════
def test_first_login_creates_user_with_sub_only(app_client, db_session):
    """首登後 `app_user` 只多一列,且該表**沒有** email / 密碼欄。

    契約 §4.2:業務庫只存 `sub`。schema 層的斷言比行為層強——
    行為可以「現在剛好沒寫」,而欄位不存在就是寫不進去。
    """
    from sqlalchemy import inspect

    from app.models import AppUser

    http, transport = app_client
    before = db_session.query(AppUser).count()
    _login(http, transport)
    after = db_session.query(AppUser).count()
    assert after == before + 1, f"首登應只多一列,實際 {before} → {after}"

    cols = {c["name"] for c in inspect(db_session.get_bind()).get_columns("app_user")}
    forbidden = {"email", "password", "password_hash", "mail", "name", "full_name"}
    assert not (cols & forbidden), (
        f"🔴 `app_user` 有不該存在的欄位:{sorted(cols & forbidden)}"
        "(契約 §4.2 只存 sub;`display_name` 是 §4.2a L1 的具名例外,名稱不同)"
    )
    assert "sub" in cols


def test_login_is_idempotent_no_duplicate_rows(app_client, db_session):
    """同一個 `sub` 重複登入不得長出第二列。

    🔴 這條擋的是「每次登入都 INSERT」——它在單人測試時完全看不出來,
    要等到有人登入第二次才會出現,而症狀是**同一個人有兩份角色**。
    """
    from app.models import AppUser

    http, transport = app_client
    _login(http, transport)
    n1 = db_session.query(AppUser).count()
    http.cookies.clear()
    _login(http, transport)
    db_session.expire_all()
    assert db_session.query(AppUser).count() == n1, "重複登入長出了新列"


# ═══════════════════════════════════════════════════════════════════
# 2. 自動授 reader(DEC-16)與它的邊界
# ═══════════════════════════════════════════════════════════════════
def test_first_login_grants_reader(app_client, db_session):
    """首登即有 `reader`(DEC-16 經核可的偏離)。"""
    from app.authz import ROLE_READER
    from app.models import AppUser

    http, transport = app_client
    _login(http, transport)
    user = db_session.query(AppUser).one()
    assert ROLE_READER in user.active_roles(), f"首登應有 reader,實得 {user.active_roles()}"


def test_reader_cannot_send_message_403(app_client):
    """🔴 反向測試(核可條件 C3):`reader` 打寄信端點必須 **403**。

    C1 要求「範圍寫死在自我範圍讀取」,而**過寬的角色沒有症狀**——
    功能上一切正常,只在有人寄了不該寄的信時才顯現。
    這條斷言是那個範圍唯一的證明。
    """
    http, transport = app_client
    _login(http, transport)
    r = http.post("/inbox/api/v1/messages", json={"recipient_sub": "x", "subject": "s", "body": "b"})
    assert r.status_code == 403, (
        f"reader 寄信應 403(已認證但無此權限),實得 {r.status_code}。"
        "⚠ 401 是錯的答案:那代表憑證無效,呼叫方會去查錯的方向"
    )


def test_reader_cannot_publish_announcement_403(app_client):
    """🔴 反向測試(核可條件 C3):`reader` 發公告必須 **403**。"""
    http, transport = app_client
    _login(http, transport)
    r = http.post("/inbox/api/v1/announcements", json={"subject": "s", "body": "b"})
    assert r.status_code == 403, f"reader 發公告應 403,實得 {r.status_code}"


def test_reader_can_be_disabled_per_user(app_client, db_session):
    """核可條件 C2:`reader` 可由本專案後台**單獨停用**,且再登入不得復活。

    🔴「再登入不得復活」是這條的一半,少了它整個停用是表演——
    停用之後下一次登入自動授與又把它加回來,而畫面上完全正常。
    """
    from app.authz import ROLE_READER
    from app.models import AppUser

    http, transport = app_client
    _login(http, transport)
    user = db_session.query(AppUser).one()
    from app.repo import set_role_enabled

    set_role_enabled(db_session, user.sub, ROLE_READER, enabled=False)
    db_session.commit()

    assert http.get("/inbox/api/v1/messages").status_code == 403, "停用 reader 後仍可讀"

    http.cookies.clear()
    _login(http, transport)
    db_session.expire_all()
    user = db_session.query(AppUser).one()
    assert ROLE_READER not in user.active_roles(), (
        "🔴 停用的 reader 在下一次登入時被自動授與復活了——停用因此是表演"
    )


def test_auto_grant_can_be_globally_disabled(app_client_no_autogrant, db_session):
    """核可條件 C4:portal 單方撤回後回到**全 deny**。

    撤回路徑=全域開關關掉。關掉之後首登得到**零角色**,
    業務 API 一律 403,並看得到待開通頁。
    """
    http, transport = app_client_no_autogrant
    _login(http, transport)

    from app.models import AppUser

    user = db_session.query(AppUser).one()
    assert user.active_roles() == [], f"撤回後首登應零角色,實得 {user.active_roles()}"
    assert http.get("/inbox/api/v1/messages").status_code == 403


# ═══════════════════════════════════════════════════════════════════
# 3. 待開通頁(契約 §4.3 的雞生蛋解法)
# ═══════════════════════════════════════════════════════════════════
def test_zero_role_user_gets_403_with_own_sub_shown(app_client_no_autogrant):
    """零角色使用者:業務 API 403,且待開通頁**顯示本人 `sub`**。

    契約 §4.3 的建議解法:deny-by-default 之下第一個使用者無人能開通他,
    所以待開通頁要顯示他自己的 `sub`,讓他能自助把那串字交給管理員。
    ⚠ 少了這一半,第一個使用者會卡在一個**看不出下一步是什麼**的 403。
    """
    http, transport = app_client_no_autogrant
    _login(http, transport)
    r = http.get("/inbox/pending")
    assert r.status_code == 200, f"待開通頁應可見,實得 {r.status_code}"
    assert "11111111-2222-3333-4444-555555555555" in r.text, "待開通頁沒有顯示本人的 sub"


# ═══════════════════════════════════════════════════════════════════
# 4. bootstrap 管理員清單
# ═══════════════════════════════════════════════════════════════════
def test_bootstrap_admin_applies_to_existing_pending_user(app_client_bootstrap, db_session):
    """清單比對必須**每次登入**都做,不只建號當下;且已停用者不得復活。

    🔴 upload-program 踩過:只在「建號當下」比對,對**第一個管理員**永遠不會生效
    ——他早就登入過了,而那次登入時清單還是空的(契約 §4.3)。
    """
    from app.authz import ROLE_ADMIN
    from app.models import AppUser

    http, transport = app_client_bootstrap
    _login(http, transport)
    user = db_session.query(AppUser).one()
    assert ROLE_ADMIN in user.active_roles(), (
        f"清單內的帳號登入後應升級為 admin,實得 {user.active_roles()}"
    )

    # 冪等:再登入一次不得長出重複的角色列
    from app.models import UserRole

    n = db_session.query(UserRole).filter_by(sub=user.sub, role=ROLE_ADMIN).count()
    http.cookies.clear()
    _login(http, transport)
    db_session.expire_all()
    assert db_session.query(UserRole).filter_by(sub=user.sub, role=ROLE_ADMIN).count() == n

    # 🔴 已停用者不得復活
    from app.repo import set_role_enabled

    set_role_enabled(db_session, user.sub, ROLE_ADMIN, enabled=False)
    db_session.commit()
    http.cookies.clear()
    _login(http, transport)
    db_session.expire_all()
    user = db_session.query(AppUser).one()
    assert ROLE_ADMIN not in user.active_roles(), (
        "🔴 被停用的 admin 因為還在 bootstrap 清單裡而復活了"
        "——那讓「停用」在清單成員身上永遠無效"
    )


# ═══════════════════════════════════════════════════════════════════
# 5. display_name L1 快取(契約 §4.2a)
# ═══════════════════════════════════════════════════════════════════
def test_display_name_never_read_in_authz_path():
    """🔴 授權判定不得讀 `display_name`(源碼層檢查,契約 §4.2a L1 第 4 條)。

    比照 portal-admin 那條例外的約束①與 PLM 的 `FkOnlyAuthorizationTests`:
    **以源碼檢查釘住**,不是靠行為測試。
    理由:行為測試只能證明「這條路徑現在沒讀」,而源碼檢查證明
    「**沒有任何一條路徑讀得到**」——授權拿過期資料做判斷的後果太直接。
    """
    import ast

    from tests.conftest import ROOT

    # ⚠ 一律用 `ROOT` 組路徑,不用相對路徑:`run_all.sh` 是 `cd tests/` 之後才跑
    #   pytest,而從 repo 根直接跑又是另一個 cwd。用相對路徑的測試會
    #   **在一個入口點綠、在另一個入口點紅**,而紅的訊息看起來像程式壞了。
    src = ROOT / "app/authz.py"
    assert src.exists(), "app/authz.py 不存在"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    hits = [
        n.attr for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and n.attr == "display_name"
    ]
    hits += [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and n.value == "display_name"
    ]
    assert not hits, f"🔴 `app/authz.py` 觸及了 display_name:{hits}"


def test_display_name_purge_tool_clears_bulk_and_single(app_client, db_session):
    """快取清除工具必須**真的清得掉**,單筆與整批都要。

    契約 §4.2a L1 第 7 條要求附整批清除工具。
    ⚠ 這條測試存在的理由:一個永遠成功卻什麼都沒清的工具,
    比沒有工具更糟——它讓人以為已經清了。
    """
    from app.models import AppUser
    from app.repo import purge_display_names

    http, transport = app_client
    _login(http, transport)
    user = db_session.query(AppUser).one()
    assert user.display_name, "前置:登入時應已寫入 L1 快取"

    # 單筆
    assert purge_display_names(db_session, sub=user.sub) == 1
    db_session.commit()
    db_session.expire_all()
    assert db_session.query(AppUser).one().display_name is None, "單筆清除沒有生效"

    # 整批(先讓它再長回來)
    http.cookies.clear()
    _login(http, transport)
    db_session.expire_all()
    assert db_session.query(AppUser).one().display_name
    assert purge_display_names(db_session) >= 1
    db_session.commit()
    db_session.expire_all()
    assert all(u.display_name is None for u in db_session.query(AppUser).all()), "整批清除沒有生效"


def test_display_name_only_written_from_own_login_token():
    """`display_name` 的寫入路徑**只有一條**(源碼層檢查)。

    契約 §4.2a L1:僅得自本人登入 token 取得。
    做法:掃 `app/` 底下所有對 `display_name` 的**賦值**,
    斷言它們只出現在 `app/repo.py` 的建號/更新函式裡。
    ⚠ 多一條寫入路徑不會有錯誤訊息,只會讓「僅自本人 token」變成一句話。
    """
    import ast

    from tests.conftest import ROOT

    allowed = {"app/repo.py"}
    offenders = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AugAssign):
                targets = [node.target]
            for t in targets:
                if isinstance(t, ast.Attribute) and t.attr == "display_name" and rel not in allowed:
                    offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, f"🔴 display_name 有額外的寫入路徑:{offenders}"


# ═══════════════════════════════════════════════════════════════════
# 6. 角色後台
# ═══════════════════════════════════════════════════════════════════
def test_admin_backend_requires_admin_role(app_client):
    """角色後台:`reader` 進不去(403),`admin` 進得去(200)。"""
    from app.authz import ROLE_ADMIN
    from app.models import AppUser
    from app.repo import grant_role

    http, transport = app_client
    _login(http, transport)
    assert http.get("/inbox/admin/users").status_code == 403, "reader 不得進入角色後台"

    # 手動升級為 admin 後應可進入
    from app.db import session_scope

    with session_scope() as s:
        user = s.query(AppUser).one()
        grant_role(s, user.sub, ROLE_ADMIN)
    r = http.get("/inbox/admin/users")
    assert r.status_code == 200, f"admin 應可進入角色後台,實得 {r.status_code}"
    # 🔴 後台顯示 L1 快取時必須標資料時間(契約 §4.2a L1 第 3 條的精神)
    assert "更新" in r.text or "updated" in r.text.lower(), "後台未顯示快取的資料時間"
