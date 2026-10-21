@echo off
rem ============================================================
rem  Email sending bot - Web na prenajom SK
rem  Dvojklik spusti bota v tomto adresari s UTF-8 vystupom.
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"
title Email bot - Web na prenajom SK
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

py -3 webnaprenajom_sending_bot_SK.py

echo.
echo ============================================================
echo  Bot skoncil. Stav a logy su ulozene.
echo ============================================================
pause
