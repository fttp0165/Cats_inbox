#!/usr/bin/env bash
# =============================================================================
# run_all.sh — 跑 tests/ 下全部測試(bash 與 pytest 兩種,CI 與本機共用同一入口)
# 為什麼:第三條2 要求測試納入 CI;單一入口讓 CI 設定永遠不必跟著測試清單改。
# 新增測試檔只要放進 tests/(test_*.sh 或 test_*.py)就會自動被撿起。
# =============================================================================
set -u
cd "$(dirname "$0")"

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
  if python3 -m pytest -q . ; then
    echo "▶ pytest 通過"
  else
    echo "▶ pytest 失敗(若為 ModuleNotFoundError:pip install -r requirements-dev.txt)"
    FAILED=$((FAILED+1))
  fi
fi

echo "=========================================="
echo "測試群組: $((TOTAL-FAILED))/${TOTAL} 通過"
[ "$FAILED" -eq 0 ] || exit 1
