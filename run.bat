@echo off
chcp 65001 >nul
echo.
echo ========================================
echo   HE THONG KIEM TRA TRUC TUYEN
echo   Khoi dong server...
echo ========================================
echo.

REM Kiem tra Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [LOI] Khong tim thay Python! Vui long cai Python 3.8+
    pause
    exit /b 1
)

REM Cai thu vien neu chua co
echo [1/3] Kiem tra va cai dat thu vien...
python -m pip install -r requirements.txt -q

REM Tao file .env neu chua co
if not exist ".env" (
    echo [2/3] Tao file cau hinh .env...
    copy .env.example .env >nul
    echo.
    echo [QUAN TRONG] Vui long mo file .env va them API Key Gemini cua ban!
    echo Lay key mien phi tai: https://aistudio.google.com/app/apikey
    echo.
    notepad .env
)

REM Khoi dong server
echo [3/3] Khoi dong server tai http://localhost:8000
echo.
echo  Trang hoc sinh: http://localhost:8000
echo  Trang quan ly:  http://localhost:8000/admin
echo.
echo  Nhan Ctrl+C de dung server
echo ========================================
echo.
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

pause
