#!/usr/bin/env bash
# Startar en TESTVERSION av Veras Kokbok på din dator (egen testdatabas).
cd "$(dirname "$0")"
export KOKBOK_ENV=test KOKBOK_PORT=8090 KOKBOK_DB="$PWD/data/test.db"
( sleep 1; xdg-open http://127.0.0.1:8090 2>/dev/null || open http://127.0.0.1:8090 2>/dev/null ) &
exec python3 server.py
