@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo [CHYBA] Python nebol najdeny v PATH.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\activate.bat" (
    echo Vytvaranie virtualneho prostredia ...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)

python beton_classifier_cz.py
pause
