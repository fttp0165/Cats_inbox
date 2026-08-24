#!/usr/bin/env bash
# =============================================================================
# run_all.sh — 跑 tests/ 下全部測試(bash 與 pytest 兩種,CI 與本機共用同一入口)
# 為什麼:第三條2 要求測試納入 CI;單一入口讓 CI 設定永遠不必跟著測試清單改。
# 新增測試檔只要放進 tests/(test_*.sh 或 test_*.py)就會自動被撿起。
# =============================================================================
set -u
cd "$(dirname "$0")"

# 🔴 直譯器要用找的,不能寫死 `python3`:Windows 的 Git Bash 通常只有 `python`
#    (`py` 是 Windows launcher)。寫死的話整個 pytest 群組在 Windows 上一律失敗,
#    而訊息是「python3: command not found」——看起來像對方環境壞了,
#    實際上是我們的腳本挑錯直譯器。開發者在哪個 OS 不該影響測試跑不跑得動。
PY=""
for c in python3 python py; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
export PY

TOTAL=0
FAILED=0

# ── bash 測試(文件層等不需 Python 依賴者) ──
for t in test_*.sh; do
  [ -e "$t" ] || continue
  TOTAL=$((TOTAL+1))
  echo "=========================================="
  echo "▶ $t"
  echo "=========================================="
  if bash "$t"; then echo "▶ $t 通過"; else echo "▶ $t 失敗"; FAILED=$((FAILED+1)); fi
done

# ── pytest 測試(應用層/骨架層) ──
# 找不到 pytest 一律視為失敗,不靜默跳過:「沒跑」與「跑過且綠」不得長得一樣
# (共通紅線:未實際跑過的測試不得宣稱通過)。
if ls test_*.py >/dev/null 2>&1; then
  TOTAL=$((TOTAL+1))
  echo "=========================================="
  echo "▶ pytest"
  echo "=========================================="
  if [ -z "$PY" ]; then
    echo "▶ pytest 失敗:找不到 python3 / python / py,請確認 Python 已安裝且在 PATH 上"
    FAILED=$((FAILED+1))
  elif OUT="$("$PY" -m pytest -q . 2>&1)"; then
    printf '%s\n' "$OUT"
    echo "▶ pytest 通過"
    # 🔴 把 skip 數量講出來。「跳過」與「通過」在 `-q` 的最後一行看起來
    #    差不多,而 T05 的 migration 測試在沒有真 PostgreSQL 時**整批 skip**
    #    ——那不是覆蓋,是沒測。不講出來的話,「全綠」會被讀成「都驗過了」。
    SKIPPED="$(printf '%s' "$OUT" | grep -oE '[0-9]+ skipped' | tail -1)"
    if [ -n "$SKIPPED" ]; then
      echo "⚠ 其中 $SKIPPED —— 那不是通過,是沒測。"
      echo "   migration 測試需要真的 PostgreSQL:"
      echo "     eval \"\$(bash tests/pg_local.sh start)\" && bash tests/run_all.sh"
    fi
  else
    printf '%s\n' "${OUT:-}"
    echo "▶ pytest 失敗(若為 ModuleNotFoundError:pip install -r requirements-dev.txt)"
    FAILED=$((FAILED+1))
  fi
fi

echo "=========================================="
echo "測試群組: $((TOTAL-FAILED))/${TOTAL} 通過"
[ "$FAILED" -eq 0 ] || exit 1
