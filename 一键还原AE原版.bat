@echo off
chcp 65001 >nul
title AeLocalizer - restore all localization
rem Restore every localization layer this tool can reverse:
rem   1) effect names -> rewrite .aex names back to English
rem   2) param names  -> stop the engine and clear the engine dictionary
rem Run with --dry to preview without changing anything.
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "%~dp0tools\restore_all.py" %*
) else (
    py -3.12 "%~dp0tools\restore_all.py" %*
)
echo.
pause
