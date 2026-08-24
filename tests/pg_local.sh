#!/usr/bin/env bash
# =============================================================================
# 起一個**丟棄式**的本機 PostgreSQL,供 migration 測試使用(T05)。
#
# 🔴 為什麼需要它:migration 測試必須跑在**真的 PostgreSQL** 上。
#   SQLite 對 DDL 的接受度寬得多(幾乎不檢查型別、ALTER TABLE 語意也不同),
#   在 SQLite 上綠的 migration 到 PG 上可能直接失敗,而那會**發生在部署當下**。
#
# ⚠ 本機是 PostgreSQL 16;平台紅線是 **15**。
#   PG16 演練**不等於** PG15 演練 —— 它驗的是「DDL 在真的 PostgreSQL 上
#   雙向跑得動」。PG15 的演練列為 T11 於 VM 上補。
#
# 用法:
#   eval "$(bash tests/pg_local.sh start)"   # 起 server 並 export INBOX_TEST_DB_URL
#   bash tests/pg_local.sh stop
# =============================================================================
set -u
PGBIN="${PGBIN:-/usr/lib/postgresql/16/bin}"
PGDIR="${PGDIR:-/tmp/pgtest}"
PGPORT="${PGPORT:-55432}"

# 以非 root 帳號執行(postgres 拒絕以 root 啟動)
RUNAS="${RUNAS:-postgres}"

case "${1:-start}" in
  start)
    if [ ! -d "$PGDIR/base" ]; then
      rm -rf "$PGDIR"; mkdir -p "$PGDIR"; chown "$RUNAS" "$PGDIR"; chmod 700 "$PGDIR"
      su "$RUNAS" -c "PATH=$PGBIN:\$PATH initdb -D $PGDIR -U cats_inbox --auth=trust" >/dev/null 2>&1 \
        || { echo "echo '❌ initdb 失敗'" ; exit 1; }
    fi
    su "$RUNAS" -c "PATH=$PGBIN:\$PATH pg_ctl -D $PGDIR -o '-p $PGPORT -k $PGDIR' -l $PGDIR/pg.log status" >/dev/null 2>&1 \
      || su "$RUNAS" -c "PATH=$PGBIN:\$PATH pg_ctl -D $PGDIR -o '-p $PGPORT -k $PGDIR' -l $PGDIR/pg.log -w start" >/dev/null 2>&1
    # 兩個資料庫:migration 測試會 upgrade/downgrade,不能與應用測試共用
    for db in cats_inbox_mig; do
      "$PGBIN/psql" -h "$PGDIR" -p "$PGPORT" -U cats_inbox -d postgres -tAc \
        "select 1 from pg_database where datname='$db'" 2>/dev/null | grep -q 1 \
        || "$PGBIN/createdb" -h "$PGDIR" -p "$PGPORT" -U cats_inbox "$db" 2>/dev/null
    done
    echo "export INBOX_TEST_DB_URL='postgresql+psycopg://cats_inbox@/cats_inbox_mig?host=$PGDIR&port=$PGPORT'"
    ;;
  stop)
    su "$RUNAS" -c "PATH=$PGBIN:\$PATH pg_ctl -D $PGDIR -m fast stop" >/dev/null 2>&1
    echo "已停止"
    ;;
  *) echo "用法: $0 [start|stop]"; exit 2 ;;
esac
