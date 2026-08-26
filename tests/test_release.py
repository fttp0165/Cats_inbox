# -*- coding: utf-8 -*-
"""T10c 發版基礎設施紅測試(映像建置 workflow + 版本一致性)。

對應驗收:`docs/任務表.md` T10c、憲法第八條、平台紅線
「production 不用 `latest` tag;部署是改 tag 後 pull」。

🔴 **本檔釘的是「所有文件都假設它已經存在」的那一步。**
`docker-compose.yml` 引用 `ghcr.io/fttp0165/cats-inbox-api:0.1.0`,
而在 T10c 之前**沒有任何 workflow 建它** —— 每份文件都對、每個任務都完成了,
而把它們接起來的那一步不在任何人的清單上。

🔴 三道一致性檢查,每一道都對應一種**不會有錯誤訊息**的說謊:

| 檢查 | 不做的後果 |
|---|---|
| git tag == `__version__` | 版本號會顯示給使用者;不一致等於說謊,換版驗證也會判錯 |
| compose 的 tag == `__version__` | **部署的是舊映像**,而它的 health 回報舊版本 —— 看起來完全一致 |
| workflow 推的映像名 == compose 引用的 | 建一個、部另一個 |
"""

from __future__ import annotations

import re

import pytest
import yaml

from tests.conftest import ROOT

WORKFLOW = ROOT / ".github" / "workflows" / "release-image.yml"
IMAGE_REPO = "ghcr.io/fttp0165/cats-inbox-api"


@pytest.fixture(scope="module")
def workflow() -> dict:
    assert WORKFLOW.exists(), f"🔴 發版 workflow 不存在:{WORKFLOW}"
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _version() -> str:
    from app import __version__

    return __version__


# ═══════════════════════════════════════════════════════════════════
# 1. 觸發條件與權限
# ═══════════════════════════════════════════════════════════════════
def test_triggered_by_tag_push_only(workflow):
    """只在**推 tag** 時建,不在每次 push 建。

    ⚠ 每次 push 都建的話,Actions 額度會被 Docker 建置吃掉 —— 而 portal 的 CI
    正因額度用盡紅了一週(2026-08-24 查明)。症狀是**發版當下才發現建不出來**。
    ⚠ 但仍要留 `workflow_dispatch`:tag 已經推了而建置失敗時,
    沒有手動入口就只能再發一個 patch 版(第八條5:同一個 tag 不重發)。
    """
    # PyYAML 會把裸 `on:` 讀成布林 True —— 這是 GitHub Actions 檔案的老問題
    triggers = workflow.get("on") or workflow.get(True)
    assert triggers, f"workflow 沒有觸發條件:{list(workflow)}"
    assert "push" in triggers, "應由 push tag 觸發"
    tags = triggers["push"].get("tags")
    assert tags, "🔴 `push` 沒有限定 tags —— 那會變成每次 push 都建"
    assert any(t.startswith("v") for t in tags), f"tag 樣式應為 v*:{tags}"
    assert "workflow_dispatch" in triggers, "缺手動觸發入口(建置失敗時要能補建)"


def test_permissions_are_least_privilege(workflow):
    """`permissions` 明確給,而且只給需要的兩個。

    🔴 不寫 `permissions` 會沿用 repo 的預設(可能是全寫)。
    推映像只需要 `packages: write` 與 `contents: read`。
    """
    job = next(iter(workflow["jobs"].values()))
    perms = job.get("permissions") or workflow.get("permissions")
    assert perms, "🔴 沒有明確的 permissions —— 會沿用 repo 預設"
    assert perms.get("packages") == "write", f"缺 packages: write:{perms}"
    assert perms.get("contents") == "read", f"contents 應為 read:{perms}"
    assert set(perms) <= {"packages", "contents"}, f"權限給太多:{perms}"


# ═══════════════════════════════════════════════════════════════════
# 2. 🔴 不推 latest
# ═══════════════════════════════════════════════════════════════════
def test_never_pushes_latest(workflow_text):
    """🔴 **一律不推 `latest`**(平台紅線)。

    `latest` 讓「同一個 tag 在不同時間指向不同東西」,而回滾時
    **你不知道要回到哪裡**。
    ⚠ 連 `type=raw,value=latest`、`latest=true` 這類寫法都不行 ——
    所以這條用字串掃全檔,不只看某一個欄位。
    """
    for bad in ("latest=true", ":latest", "value=latest", "tag_latest"):
        assert bad not in workflow_text, f"🔴 workflow 含 `{bad}`"


# ═══════════════════════════════════════════════════════════════════
# 3. 🔴 三道一致性檢查
# ═══════════════════════════════════════════════════════════════════
def test_workflow_verifies_tag_matches_version_constant(workflow_text):
    """workflow 裡必須有一個**會失敗**的步驟,比對 git tag 與 `__version__`。

    🔴 第八條4:版本號會顯示給使用者(`/inbox/health`、頁尾),
       **tag 與常數不一致等於說謊**,而換版驗證也會判錯。
    ⚠ 靠人「打 tag 前記得改常數」是不夠的 —— 那正是第八條要寫進憲法的原因。
    """
    assert "__version__" in workflow_text, "🔴 workflow 沒有讀 `__version__`"
    # 必須真的會讓步驟失敗,不是只 echo 一行
    assert re.search(r"exit\s+1", workflow_text), "🔴 不一致時沒有 `exit 1` —— 那只是印訊息"


def test_compose_image_tag_matches_version_constant():
    """`docker-compose.yml` 的映像 tag 必須等於 `__version__`。

    🔴 這一條擋的是最安靜的那種錯:改了常數而忘了 compose ——
       部署的是**舊映像**,而它的 health 回報**舊版本**,
       所以兩者「看起來完全一致」,而你以為換版成功了。
    """
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    image = compose["services"]["cats-inbox-api"]["image"]
    assert image == f"{IMAGE_REPO}:{_version()}", (
        f"🔴 compose 的映像是 {image},而 __version__ 是 {_version()}"
    )


def test_workflow_image_repo_matches_compose(workflow_text):
    """workflow 推的映像名必須與 compose 引用的**逐字相同**。

    不同的話就是「建一個、部另一個」。⚠ 這一個在 `docker compose up` 當場會報錯
    (算是有訊息),但錯在**低峰窗口**,而那是最不該發現這件事的時刻。
    """
    assert IMAGE_REPO in workflow_text, (
        f"🔴 workflow 沒有推 `{IMAGE_REPO}` —— 與 compose 引用的不一致"
    )


# ═══════════════════════════════════════════════════════════════════
# 4. 發版 SOP(第八條要能照著做)
# ═══════════════════════════════════════════════════════════════════
def test_release_sop_exists_with_four_required_sections():
    """`docs/發版SOP.md` 必須列出第八條的 **title 格式**與 **四段 content**。

    🔴 憲法寫了「content 必含四段」,而沒有一份清單把它變成可照做的步驟 ——
       那條規則就只在有人記得的時候生效。
    """
    sop = ROOT / "docs" / "發版SOP.md"
    assert sop.exists(), "🔴 發版 SOP 不存在"
    text = sop.read_text(encoding="utf-8")
    # 🔴 驗**標題**(`## X`),不是驗字串在不在全文裡。突變檢查抓到的:
    #    把 `## 部署注意` 改成 `## 其他` 之後這一條仍然綠 ——
    #    因為「部署注意」四個字在別處的說明文字裡也出現過。
    #    **斷言的粒度比它宣稱保護的性質粗**(與 T09b/T10b 那幾個洞同型)。
    for section in ("本版內容", "對現有資料的影響", "部署注意", "測試"):
        assert f"## {section}" in text, f"SOP 缺第八條的必含**段落標題**:## {section}"
    assert "vX.Y.Z" in text, "SOP 沒有寫出 title 格式"


def test_release_sop_keeps_honest_status_table():
    """SOP 的 §0 必須留著那張「哪些跑過了、哪些還沒」的表,且**每列都帶狀態記號**。

    🔴 這一條取代原本的 `assert "尚未" in text`(D03 改寫)。原斷言釘的是一個
       **當時為真的暫時狀態** —— T10c 寫下它時四件事全都沒跑過。
       它會在四件事**全部完成**的那天才紅,而那天的正確反應是刪掉 §0;
       於是人會把「尚未」三個字補回文件裡讓測試閉嘴,**文件反而變成假的**。
    ⚠ 所以改成釘**不會到期的性質**:那張表必須存在,而且不准有哪一列
      沒有狀態記號 —— 空白會被讀成「應該沒問題」,而它可能是「沒人去看」。
      「哪一件還沒跑」交給文件自己講,測試只保證**這張誠實表不會消失**。
    """
    text = (ROOT / "docs" / "發版SOP.md").read_text(encoding="utf-8")
    lines = text.splitlines()

    # §0 的節標題:允許標題文字改寫,但那一節必須還在(以 `## 0.` 為錨)
    starts = [i for i, ln in enumerate(lines) if ln.startswith("## 0.")]
    assert starts, "🔴 發版 SOP 少了 §0 那張「哪些跑過了、哪些還沒」的表"

    # 取 §0 到下一個 `## ` 之間的表格資料列(跳過表頭與分隔線)
    start = starts[0]
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    rows = [
        ln for ln in lines[start:end]
        if ln.startswith("|") and set(ln) - set("|-: ")  # 排除 |---|---| 分隔線
    ]
    assert len(rows) >= 4, (
        f"🔴 §0 的表只有 {len(rows)} 列(含表頭)—— 四個步驟至少要各佔一列"
    )
    # 第一列是表頭,其餘每一列都必須帶 ✅ 或 ⏳
    for row in rows[1:]:
        assert "✅" in row or "⏳" in row, (
            f"🔴 §0 有一列沒帶狀態記號(✅ 已跑過 / ⏳ 尚未):{row.strip()[:60]}"
        )


# ═══════════════════════════════════════════════════════════════════
# 5. 🔴 workflow 裡的 shell 本身要能跑(語法層)
# ═══════════════════════════════════════════════════════════════════
def test_every_run_block_is_valid_shell(workflow):
    """每一個 `run:` 區塊都要通過 `bash -n`。

    🔴 workflow 裡的 shell 語法錯誤**只會在發版當下**顯現 —— 而那時 tag 已經
       推出去了,而同一個 tag 不能重發(第八條5)。
    ⚠ `bash -n` 只驗語法,不驗語意(它不會知道 `sed` 的表達式對不對)——
      所以那兩個 `sed` 另外在本機對真檔案實測過,結果記在 T10c 的 dev-log。
    """
    import subprocess

    job = next(iter(workflow["jobs"].values()))
    scripts = [s["run"] for s in job["steps"] if s.get("run")]
    assert scripts, "workflow 沒有任何 run: 區塊 —— 這支測試會變成空檢查"
    for script in scripts:
        r = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
        assert r.returncode == 0, f"🔴 run 區塊語法錯誤:{r.stderr[:300]}"


def test_smoke_step_runs_the_image_before_pushing(workflow_text):
    """推之前必須先把映像**跑起來**驗一次。

    🔴 「建得起來」與「跑得起來」是兩件事。少了這一步,壞掉的映像會被推到
       registry,而發現的時機是 VM 上 `docker compose up -d` 之後 ——
       也就是**低峰窗口裡**。
    ⚠ 順序也要對:`load: true`(先建到本機)必須出現在 `push: true` 之前。
    """
    assert "docker run" in workflow_text, "🔴 沒有把映像跑起來驗過就推"
    assert "/inbox/health" in workflow_text, "🔴 冒煙沒有打健康檢查"
    load_at = workflow_text.find("load: true")
    push_at = workflow_text.find("push: true")
    assert load_at != -1 and push_at != -1, "缺 load/push 步驟"
    assert load_at < push_at, "🔴 先推才驗 —— 順序反了"
