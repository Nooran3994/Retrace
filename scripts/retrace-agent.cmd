@echo off
REM Retrace agent loop launcher (Task Scheduler entry point)
REM Runs the persistent capture+detect loop every 1 minute.
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0retrace-run-agent.ps1" >> "%LOCALAPPDATA%\retrace\agent.log" 2>&1