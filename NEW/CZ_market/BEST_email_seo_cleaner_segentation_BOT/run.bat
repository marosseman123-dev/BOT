@echo off
REM Unified Lead + SEO + Cleanup bot -- self-installing isolated venv.
setlocal enabledelayedexpansion
cd /d "%~dp0"
set "VENV=.venv_unified"
set "PY=%VENV%\Scripts\python.exe"
echo [unibot] Folder: %CD%

where python >nul 2>&1
if errorlevel 1 ( echo [unibot] ERROR: Python not on PATH. Install 3.9+ and retry. & goto :end )
if not exist "%PY%" (
    echo [unibot] Creating isolated venv %VENV% ...
    python -m venv "%VENV%"
    if errorlevel 1 ( echo [unibot] ERROR: venv creation failed. & goto :end )
    set "FRESH_ENV=1"
)
if defined FRESH_ENV goto :install
if not exist "%VENV%\.deps_ok" goto :install
goto :doctor
:install
echo [unibot] Installing dependencies ...
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 ( echo [unibot] ERROR: pip install failed. & goto :end )
> "%VENV%\.deps_ok" echo ok
:doctor
echo [unibot] Verifying imports ...
"%PY%" -c "import sys,httpx,bs4,lxml,pandas,brotli; sys.path.insert(0,'.'); import unibot; print('[unibot] deps OK on', sys.version.split()[0])"
if errorlevel 1 (
    echo [unibot] Import check failed ^(deps changed?^) -- reinstalling once ...
    del "%VENV%\.deps_ok" >nul 2>&1
    if not defined RETRIED ( set RETRIED=1 & goto :install )
    echo [unibot] ERROR: import check still failing after reinstall.
    goto :end
)
:launch
echo [unibot] Launching ...
"%PY%" -B -m unibot %*
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" ( echo [unibot] Exited with code %RC%. See out\^<run^>\unified.log ) else ( echo [unibot] Finished. Outputs in out\ )
:end
echo.
echo [unibot] Press any key to close...
pause >nul
endlocal
