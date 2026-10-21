@echo off
chcp 65001 >nul
cd /d "%~dp0"
title ART4YOU - CZ email bot

REM --- najdi Python (najprv launcher "py", potom "python") ---
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY (
    echo [CHYBA] Python nie je nainstalovany na tomto pocitaci.
    echo Stiahni ho z https://www.python.org/downloads/ a pri instalacii
    echo ZASKRTNI moznost "Add Python to PATH". Potom spusti tento subor znova.
    echo.
    pause
    exit /b 1
)

REM --- over a pripadne jednorazovo doinstaluj potrebne kniznice ---
%PY% -c "import googleapiclient, google.oauth2" 1>nul 2>nul
if errorlevel 1 (
    echo Instalujem potrebne kniznice, chvilu to potrva...
    %PY% -m pip install --disable-pip-version-check google-api-python-client google-auth
    if errorlevel 1 (
        echo [CHYBA] Nepodarilo sa nainstalovat kniznice. Skontroluj pripojenie na internet.
        echo.
        pause
        exit /b 1
    )
)

echo ============================================================
echo   ART4YOU - rozosielanie emailov pre CZ trh
echo ============================================================
%PY% "%~dp0PROD_sending_BOT.py"
echo ============================================================
echo Bot skoncil. Toto okno mozes zavriet.
pause
