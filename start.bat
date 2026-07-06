@echo off
setlocal EnableExtensions
title HUMAM TAIBEH'S CORE ARCHITECTURE - Launcher

set "SCRIPT_DIR=%~dp0"
set "PS1_PATH=%SCRIPT_DIR%HT_Core_Architecture.ps1"

:: ============================================================
::  ADMIN ELEVATION CHECK
:: ============================================================
net session >nul 2>&1
if %errorlevel% NEQ 0 (
    echo.
    echo    Administrator privileges are required. Requesting elevation...
    echo.
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

:: ============================================================
::  VERIFY THE .PS1 FILE EXISTS NEXT TO THIS LAUNCHER
:: ============================================================
if not exist "%PS1_PATH%" (
    echo.
    echo    [ERROR] Could not find HT_Core_Architecture.ps1 in:
    echo        %SCRIPT_DIR%
    echo.
    echo    Please make sure this .bat file sits in the same folder as
    echo    HT_Core_Architecture.ps1, then run it again.
    echo.
    pause
    exit /b 1
)

:: ============================================================
::  LAUNCH
:: ============================================================
echo.
echo    Launching HUMAM TAIBEH'S CORE ARCHITECTURE...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1_PATH%"

exit /b