#!/usr/bin/env bash
# =============================================================================
# test_docs.sh — 文件層測試(T00 的紅測試)
#
# 為什麼有這支測試:
#   本 repo 的憲法(CLAUDE.md)第四條要求正式文件 md+HTML 並存、light 主題、
#   第七條要求 metadata 與版本歷史。文件不合格不會有任何錯誤訊息——
#   只會在幾週後變成「分不清哪份是現況」。這支測試把那些要求釘成可執行的斷言。
#
# 用法: bash tests/test_docs.sh   (在 repo 根目錄或 tests/ 下皆可)
# 回傳: 全過 exit 0;任一失敗 exit 1,並以「位置/原因」格式列出失敗項。
# =============================================================================
set -u
cd "$(dirname "$0")/.."   # 一律回到 repo 根目錄執行,路徑斷言才穩定

# 直譯器由 run_all.sh 傳入(export PY);單獨執行本檔時自己找一次。
# 理由同 run_all.sh:Windows 的 Git Bash 通常只有 `python`,寫死 `python3` 會讓
# 「HTML 是否漂移」這條檢查在 Windows 上永遠失敗,而症狀看起來像文件真的漂移了。
if [ -z "${PY:-}" ]; then
  for c in python3 python py; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
  done
fi

PASS=0
FAIL=0

# ok/ng:統一的斷言輸出格式(位置/原因,對齊憲法第九條的錯誤格式要求)
ok()  { PASS=$((PASS+1)); echo "  ✅ $1"; }
ng()  { FAIL=$((FAIL+1)); echo "  ❌ $1 — $2"; }

# check_meta <檔案>:驗第七條 metadata 三行與版本歷史表
check_meta() {
  local f="$1"
  grep -q "^\*\*建立日期:\*\*" "$f" && grep -q "^\*\*最後更新:\*\*" "$f" \
    && grep -q "^\*\*版本:\*\*" "$f" \
    && ok "$f metadata 三行齊" || ng "$f" "缺 建立日期/最後更新/版本 之一(第七條1)"
  grep -q "^## 版本歷史" "$f" \
    && ok "$f 有版本歷史表" || ng "$f" "缺「## 版本歷史」(第七條2)"
}

echo "[1] 憲法存在且附錄 A 已特化"
if [ -f CLAUDE.md ]; then
  ok "CLAUDE.md 存在"
  grep -q "cats-inbox" CLAUDE.md && ok "附錄 A 已填 cats-inbox" \
    || ng "CLAUDE.md" "附錄 A 未特化(找不到 cats-inbox)"
  # 憲法自己也是文件,第七條一體適用——改憲法時忘了升版是最容易發生的事
  check_meta CLAUDE.md
  # 第九條 11/12/13:指令必須標明在哪執行、改哪個檔、動到誰的東西(v1.5 改版)
  # 🔴 三行分開驗,不合成一次 grep —— 合起來的話,少了其中一種標示時
  #    錯誤訊息只會說「格式不在案」,而讀的人不知道少的是哪一個。
  grep -q "在哪執行" CLAUDE.md \
    && ok "第九條11「在哪執行」格式在案" \
    || ng "CLAUDE.md" "缺第九條11 的「🖥️ 在哪執行:<平台> · 工作目錄 <路徑>」格式"
  grep -q "編輯哪個檔" CLAUDE.md \
    && ok "第九條11「編輯哪個檔」格式在案" \
    || ng "CLAUDE.md" "缺第九條11 的「📄 編輯哪個檔:<完整路徑>」格式(檔名藏在指令裡=讀的人要自己推)"
  grep -q "動到誰的東西" CLAUDE.md \
    && ok "第九條13「動到誰的東西」在案" \
    || ng "CLAUDE.md" "缺第九條13 —— 對別的專案擁有的資源下指令時無處標示"
  grep -q "Cats VM" CLAUDE.md \
    && ok "A.4 已填指令位置的實際值" \
    || ng "CLAUDE.md" "A.4 未填平台/工作目錄實際值(條文無法執行)"
else
  ng "CLAUDE.md" "檔案不存在"
fi

# ── 正式文件鏈:清單**自 render_docs.py 的 TARGETS 自動產生** ─────────
# 🔴 為什麼不手寫:D01 已經記過一次 —— **新文件不登記進守門的清單,`--check`
#    會全綠**,因為它根本沒去看那個檔。而那份清單原本手寫了三次,
#    也就是新增一份文件要記得改四個地方(TARGETS + 這裡三處)。
#    自動產生之後,「新增文件」與「納入守門」變成同一個動作。
# ⚠ 解析失敗會讓清單變空,而**空清單的迴圈是全綠的** —— 所以下面立刻
#    斷言數量下限;數量對不上就紅,而不是靜靜地什麼都沒驗。
DOC_MD=$(sed -n 's|^ *ROOT / "docs" / "\(.*\)",$|docs/\1|p; s|^ *ROOT / "\(README.md\)",$|\1|p' tools/render_docs.py)
DOC_COUNT=$(printf '%s\n' "$DOC_MD" | grep -c '\.md$' || true)
if [ "$DOC_COUNT" -ge 7 ]; then
  ok "文件鏈清單自 TARGETS 產生($DOC_COUNT 份)"
else
  ng "tools/render_docs.py" "只解析到 $DOC_COUNT 份文件(清單可能沒解析成功;守門會變成空檢查)"
fi
DOC_HTML=$(printf '%s\n' "$DOC_MD" | sed 's|\.md$|.html|')

echo "[2] 正式文件鏈:md 權威版存在且合第七條"
for f in $DOC_MD; do
  if [ -f "$f" ]; then check_meta "$f"; else ng "$f" "檔案不存在"; fi
done

echo "[3] 正式文件 HTML 發布版存在(第四條4)"
for f in $DOC_HTML; do
  [ -f "$f" ] && ok "$f 存在" || ng "$f" "HTML 版不存在(第四條4:md+HTML 並存)"
done

echo "[4] HTML 為 light 主題、無外部依賴(第四條1/2、契約 §4.10 精神)"
for f in $DOC_HTML; do
  [ -f "$f" ] || continue
  if grep -q "prefers-color-scheme: *dark" "$f"; then
    ng "$f" "含 prefers-color-scheme: dark(第四條2 禁止)"
  else
    ok "$f 無深色自動切換"
  fi
  if grep -qE 'src="https?://|href="https?://(cdn|fonts|unpkg|jsdelivr)' "$f"; then
    ng "$f" "引用外部 CDN/字型(單檔離線可開被破壞)"
  else
    ok "$f 無外部資源引用"
  fi
done

echo "[5] md ↔ HTML 未漂移(第四條6:HTML 由 render_docs.py 產生)"
if [ -f tools/render_docs.py ]; then
  if [ -z "${PY:-}" ]; then
    ng "tools/render_docs.py --check" "找不到 python3 / python / py,無法驗證 HTML 是否同步"
  elif "$PY" tools/render_docs.py --check >/dev/null 2>&1; then
    ok "render_docs.py --check 通過(HTML 與 md 同步)"
  else
    ng "tools/render_docs.py --check" "HTML 與 md 內容漂移,重跑 $PY tools/render_docs.py"
  fi
else
  ng "tools/render_docs.py" "工具不存在"
fi

echo "[6] 開發日誌與紅線"
ls docs/dev-logs/*T00* >/dev/null 2>&1 && ok "T00 開發日誌存在" \
  || ng "docs/dev-logs/" "缺 T00 日誌(第六條)"
grep -q "^\.env$" .gitignore 2>/dev/null && ok ".gitignore 含 .env" \
  || ng ".gitignore" "未忽略 .env(共通紅線)"
# ── 🔴 進度表與任務表的狀態必須一致(D04)────────────────────────────
# 進度表**複製**了任務表的狀態欄,而複製會漂移。更麻煩的是進度表是唯一一份
# **設計上就要給人掃一眼就信**的文件 —— 它錯的時候傷害最大。
# 🔴 這條擋的是「更新了一邊忘了另一邊」,而那是實際會發生的形狀。
#    ⚠ 它擋不住「兩邊一起寫錯」,那條路仍然靠人。
#    ⚠ 它在寫的當天就抓到三個錯的數字(以為 17 個 T 任務 / 完成 14 / 未動工 5,
#      實際 23 個 T 列 / 完成 21 / 未完成 6)—— 拆分出來的
#      T03b、T09b、T10b、T10c、T11a、T11b 六列被漏數。
echo "[7] 進度表與任務表的狀態一致(D04)"
STATUS_CMP="$("$PY" - <<'PY'
import re, pathlib
root = pathlib.Path(".")

def rows():
    """自任務表抽出 (編號, 是否完成)。以最後一欄有沒有 ✅ 判定。"""
    for ln in (root / "docs" / "任務表.md").read_text(encoding="utf-8").splitlines():
        if not ln.startswith("|") or set(ln) <= set("|-: "):
            continue
        c = [x.strip() for x in ln.strip("|").split("|")]
        if len(c) < 7 or c[0] in ("編號", "版本"):
            continue
        num = re.sub(r"[*`]", "", c[0])
        if re.match(r"^(T\d|D\d)", num):
            yield num, ("✅" in c[-1])

table = list(rows())
# 🔴 抽取壞掉時清單會變空,而**空清單的比對是全綠的** —— 先斷言數量下限。
if len(table) < 20:
    print(f"NG 只從任務表抽到 {len(table)} 列 —— 抽取壞了,這條等於沒測")
    raise SystemExit
want_done = [n for n, d in table if d]
want_todo = [n for n, d in table if not d]

prog = (root / "docs" / "進度表.md").read_text(encoding="utf-8")

def listed(label):
    m = re.search(rf"^\*\*{label}:\*\*(.+)$", prog, re.M)
    if not m:
        return None
    return [x.strip() for x in m.group(1).replace("`", "").split("·") if x.strip()]

got_done, got_todo = listed("已完成"), listed("未完成")
if got_done is None or got_todo is None:
    print("NG 進度表缺「已完成:」或「未完成:」那一行(守門靠它比對)")
    raise SystemExit
for label, want, got in (("已完成", want_done, got_done), ("未完成", want_todo, got_todo)):
    if want != got:
        miss = [x for x in want if x not in got]
        extra = [x for x in got if x not in want]
        print(f"NG 進度表的「{label}」與任務表不符 —— 少了 {miss or '無'};多了 {extra or '無'}")
        raise SystemExit
print(f"OK 進度表與任務表逐項相符(完成 {len(want_done)} / 未完成 {len(want_todo)})")
PY
)"
case "$STATUS_CMP" in
  OK*) ok "${STATUS_CMP#OK }" ;;
  *)   ng "docs/進度表.md" "${STATUS_CMP#NG }" ;;
esac

# IdP 已定案 Keycloak(portal D1);任何殘留 Authentik 都是過期文案
if grep -riq "authentik" --include="*.md" --include="*.py" --include="*.html" . 2>/dev/null; then
  ng "repo" "殘留 Authentik 字樣(IdP 已定案 Keycloak)"
else
  ok "零 Authentik 殘留"
fi

echo ""
echo "結果: ${PASS} 過 / ${FAIL} 敗"
[ "$FAIL" -eq 0 ] || exit 1
