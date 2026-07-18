@echo off
title Humam Windows Architecture Launcher

rem Anchor to this script's own folder so shortcuts / "Run as admin"
rem launches (which start in System32) still find the project files.
pushd "%~dp0"

if not exist "src\frontend\main.py" (
    echo ERROR: src\frontend\main.py not found.
    popd
    pause
    exit /b 1
)

if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else (
    echo WARNING: .venv not found - falling back to the system Python.
)

python src\frontend\main.py
set EXITCODE=%ERRORLEVEL%
popd
if not %EXITCODE%==0 pause
exit /b %EXITCODE%
