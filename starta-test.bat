@echo off
REM Startar en TESTVERSION av Veras Kokbok pa din dator (egen testdatabas).
cd /d "%~dp0"
set KOKBOK_ENV=test
set KOKBOK_PORT=8090
set KOKBOK_DB=%~dp0data\test.db
start "" http://127.0.0.1:8090
python server.py || py server.py
pause
