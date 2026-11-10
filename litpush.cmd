@echo off
rem ===================================================================
rem  litpush command wrapper
rem
rem  Picks a Python 3.11+ interpreter, in this order:
rem    1) %LITPUSH_PYTHON%          set this if you keep several installs
rem    2) py -3.12  (Windows Python launcher)
rem    3) python    (whatever is first on PATH)
rem
rem  Usage:  litpush.cmd run
rem ===================================================================
setlocal
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if defined LITPUSH_PYTHON (
    "%LITPUSH_PYTHON%" -u "%~dp0run.py" %*
    endlocal & exit /b %ERRORLEVEL%
)

where py >nul 2>nul
if not errorlevel 1 (
    py -3.12 -u "%~dp0run.py" %*
    if not errorlevel 1 (endlocal & exit /b 0)
    if not errorlevel 9009 (endlocal & exit /b %ERRORLEVEL%)
)

python -u "%~dp0run.py" %*
endlocal & exit /b %ERRORLEVEL%
