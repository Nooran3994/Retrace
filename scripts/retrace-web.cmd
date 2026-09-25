@echo off
REM Retrace web UI launcher (Task Scheduler entry point)
REM Starts the local-only web UI on 127.0.0.1:8765 at logon.
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0retrace-run-web.ps1" >> "%LOCALAPPDATA%\retrace\web.log" 2>&1