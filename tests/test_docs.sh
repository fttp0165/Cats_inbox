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
  # 第九條11:指令必須標明「在哪裡下」;A.4 必須填上本專案的實際值,否則條文無從執行
  grep -q "【機器】【路徑】【專案】" CLAUDE.md \
    && ok "第九條11 指令位置標示格式在案" \
    || ng "CLAUDE.md" "缺第九條11 的 【機器】【路徑】【專案】 格式"
  grep -q "CATS VM" CLAUDE.md \
    && ok "A.4 已填指令位置的實際值" \
    || ng "CLAUDE.md" "A.4 未填機器/路徑實際值(條文無法執行)"
else
  ng "CLAUDE.md" "檔案不存在"
fi

echo "[2] 正式文件鏈:md 權威版存在且合第七條"
for f in docs/開發計畫書.md docs/任務表.md docs/TDD測試計畫表.md README.md; do
  if [ -f "$f" ]; then check_meta "$f"; else ng "$f" "檔案不存在"; fi
done

echo "[3] 正式文件 HTML 發布版存在(第四條4)"
for f in docs/開發計畫書.html docs/任務表.html docs/TDD測試計畫表.html README.html; do
  [ -f "$f" ] && ok "$f 存在" || ng "$f" "HTML 版不存在(第四條4:md+HTML 並存)"
done

echo "[4] HTML 為 light 主題、無外部依賴(第四條1/2、契約 §4.10 精神)"
for f in docs/開發計畫書.html docs/任務表.html docs/TDD測試計畫表.html README.html; do
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
# IdP 已定案 Keycloak(portal D1);任何殘留 Authentik 都是過期文案
if grep -riq "authentik" --include="*.md" --include="*.py" --include="*.html" . 2>/dev/null; then
  ng "repo" "殘留 Authentik 字樣(IdP 已定案 Keycloak)"
else
  ok "零 Authentik 殘留"
fi

echo ""
echo "結果: ${PASS} 過 / ${FAIL} 敗"
[ "$FAIL" -eq 0 ] || exit 1
