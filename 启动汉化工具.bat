@echo off
setlocal
chcp 65001 >nul
title AeLocalizer (源码版)
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m app.main
) else (
    py -3.12 -m app.main
)
if errorlevel 1 pause
