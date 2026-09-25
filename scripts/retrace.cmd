@echo off
REM retrace.cmd — self-contained Windows launcher (cmd.exe / PowerShell).
REM
REM Makes the `retrace` command available from ANY terminal and ANY
REM directory WITHOUT requiring pip, venv, or PATH surgery beyond one setx.
REM
REM How it works:
REM   1. Locate the Retrace repo root (this file lives in <repo>\scripts\).
REM   2. Ensure the repo root is importable so `python -m retrace.cli`
REM      resolves to THIS checkout (not a stale site-packages copy).
REM   3. Run python -m retrace.cli with all arguments passed through.
REM
REM Install (done automatically by install.ps1):
REM   setx PATH "%PATH%;<repo>\scripts"
REM
setlocal

REM --- 1. Resolve repo root (%~dp0 = scripts\ dir) ---
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"

REM --- 2. Locate python (prefer explicit, then PATH) ---
set "PYTHON_EXE="
if defined RETRACE_PYTHON set "PYTHON_EXE=%RETRACE_PYTHON%"
if not defined PYTHON_EXE if exist "C:\Python314\python.exe" set "PYTHON_EXE=C:\Python314\python.exe"
if not defined PYTHON_EXE set "PYTHON_EXE=python"

REM --- 3. Ensure THIS repo is importable from any CWD ---
set "PYTHONPATH=%REPO_ROOT%;%PYTHONPATH%"

REM --- 4. Run ---
"%PYTHON_EXE%" -m retrace.cli %*
exit /b %ERRORLEVEL%