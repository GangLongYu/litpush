@echo off
rem ===================================================================
rem  litpush daily runner (invoked by Windows Task Scheduler)
rem  - force UTF-8 so Chinese titles survive in the log
rem  - up to 3 attempts, 5 minutes apart: a freshly booted machine or
rem    a not-yet-connected VPN often has no working DNS/TLS at 08:30
rem  - rotate the log when it grows past 2 MB
rem  NOTE: keep this file ASCII-only; cmd.exe uses the OEM codepage.
rem ===================================================================
setlocal
set "ROOT=%~dp0.."
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%ROOT%"

set "PY=python"
if defined LITPUSH_PYTHON set "PY=%LITPUSH_PYTHON%"
where py >nul 2>nul
if not errorlevel 1 (
    py -3.12 -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PY=py -3.12"
)

if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"
for %%F in ("%ROOT%\logs\daily.log") do if %%~zF GTR 2097152 move /y "%ROOT%\logs\daily.log" "%ROOT%\logs\daily.1.log" >nul

set "ATTEMPT=0"
:attempt
set /a ATTEMPT+=1
echo. >> "%ROOT%\logs\daily.log"
echo ===== %date% %time% attempt %ATTEMPT% ===== >> "%ROOT%\logs\daily.log"
"%PY%" -u "%ROOT%\run.py" run %* >> "%ROOT%\logs\daily.log" 2>&1
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" goto done
if %ATTEMPT% GEQ 3 goto done
echo [retry] exit=%RC%, waiting 300s >> "%ROOT%\logs\daily.log"
ping -n 301 127.0.0.1 >nul
goto attempt

:done
if not "%RC%"=="0" echo [error] exit code %RC% >> "%ROOT%\logs\daily.log"
endlocal & exit /b %RC%
