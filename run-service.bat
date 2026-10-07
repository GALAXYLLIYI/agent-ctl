@echo off
REM Runs the service in its own window. Launched by the launcher .bat via 'start'.
REM Kept as a separate file so the launcher never needs nested quotes
REM (cmd does not understand \" escaping - that bug cost us two failed runs).
REM NOTE: keep this file pure ASCII. A Chinese filename in a comment is enough
REM to reintroduce the GBK-misparse bug that killed an earlier version.
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo   AgentCtl Service
echo ============================================================
echo.
whoami /groups | findstr /C:"Mandatory Label"
echo.
echo   Must be Medium to control Medium-integrity windows.
echo   DO NOT CLOSE THIS WINDOW while the agent is working.
echo   Emergency stop: Ctrl+Alt+Home  (or Ctrl+C here)
echo.

if not exist "dist\AgentCtl\AgentCtl.exe" (
  echo [ERROR] not found: dist\AgentCtl\AgentCtl.exe
  echo.
  pause
  exit /b 1
)

"dist\AgentCtl\AgentCtl.exe" serve
set RC=%ERRORLEVEL%

echo.
echo ==== SERVICE PROCESS ENDED (exit code %RC%) ====
echo.
pause
