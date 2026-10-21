#!/usr/bin/env bash
# Hämtar senaste versionen från GitHub och startar om kokboken.
# Tar en säkerhetskopia av databasen först.
set -euo pipefail
cd "$(dirname "$0")"

DB="data/kokbok.db"
if [ -f "$DB" ]; then
  mkdir -p data/backup
  STAMP=$(date +%Y-%m-%d_%H%M)
  python3 -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()" \
    "$DB" "data/backup/kokbok_$STAMP.db"
  # behåll de 20 senaste
  ls -1t data/backup/kokbok_*.db | tail -n +21 | xargs -r rm --
  echo "Säkerhetskopia: data/backup/kokbok_$STAMP.db"
fi

git pull --ff-only
sudo systemctl restart veras-kokbok
sleep 1
curl -fsS "http://127.0.0.1:${KOKBOK_PORT:-8090}/api/health" && echo
echo "Kör nu version: $(git describe --tags --always)"
