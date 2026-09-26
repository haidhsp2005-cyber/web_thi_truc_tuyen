@echo off
chcp 65001 >nul
title Hệ Thống Kiểm Tra Trực Tuyến 2026
echo.
echo ========================================================
echo   HE THONG KIEM TRA TRUC TUYEN 2026
echo   Dang khoi dong server...
echo ========================================================
echo.

REM 1. Tu dong tim duong dan Python tren he thong
set "PY_CMD="

REM Kiem tra C:\ProgramData\miniconda3\python.exe (duong dan chinh xac tren may)
if exist "C:\ProgramData\miniconda3\python.exe" (
    set "PY_CMD=C:\ProgramData\miniconda3\python.exe"
    set "PATH=C:\ProgramData\miniconda3;C:\ProgramData\miniconda3\Scripts;C:\ProgramData\miniconda3\Library\bin;%PATH%"
)

REM Neu chua tim thay, thu lenh python truc tiep
if not defined PY_CMD (
    python --version >nul 2>&1
    if %errorlevel% equ 0 set "PY_CMD=python"
)

REM Neu chua tim thay, thu lenh py launcher
if not defined PY_CMD (
    py --version >nul 2>&1
    if %errorlevel% equ 0 set "PY_CMD=py"
)

REM Kiem tra cac thu muc cai dat pho bien khac
if not defined PY_CMD if exist "%USERPROFILE%\miniconda3\python.exe" (
    set "PY_CMD=%USERPROFILE%\miniconda3\python.exe"
    set "PATH=%USERPROFILE%\miniconda3;%USERPROFILE%\miniconda3\Scripts;%PATH%"
)
if not defined PY_CMD if exist "%USERPROFILE%\anaconda3\python.exe" (
    set "PY_CMD=%USERPROFILE%\anaconda3\python.exe"
    set "PATH=%USERPROFILE%\anaconda3;%USERPROFILE%\anaconda3\Scripts;%PATH%"
)
if not defined PY_CMD if exist "C:\miniconda3\python.exe" (
    set "PY_CMD=C:\miniconda3\python.exe"
    set "PATH=C:\miniconda3;C:\miniconda3\Scripts;%PATH%"
)
if not defined PY_CMD (
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do (
        if exist "%%D\python.exe" (
            set "PY_CMD=%%D\python.exe"
            set "PATH=%%D;%%D\Scripts;%PATH%"
        )
    )
)

if not defined PY_CMD (
    echo [LOI] Khong tim thay Python! Vui long cai Python 3.8+ hoac them vao PATH.
    pause
    exit /b 1
)

echo [OK] Da tim thay Python:
"%PY_CMD%" --version
echo.

REM 2. Tao file .env neu chua co
if not exist ".env" (
    echo [1/2] Tao file cau hinh .env...
    copy .env.example .env >nul
    echo [OK] Da tao file .env
)

REM 3. Khoi dong server
echo [2/2] Khoi dong server tai cong 8000...
echo.
echo ========================================================
echo  * Trang hoc sinh thi:  http://localhost:8000
echo  * Trang quan ly:       http://localhost:8000/admin
echo  * Cloudflare Tunnel:   Tro vao http://localhost:8000
echo.
echo  [!] De dung server, nhan to hop phim Ctrl + C
echo ========================================================
echo.

"%PY_CMD%" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

pause
