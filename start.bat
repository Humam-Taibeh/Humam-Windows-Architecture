@echo off
title Humam Windows Architecture Launcher

if not exist "src\frontend\main.py" (
    echo ERROR: src\frontend\main.py not found.
    pause
    exit /b 1
)

call .venv\Scripts\activate
python src\frontend\main.py
pause