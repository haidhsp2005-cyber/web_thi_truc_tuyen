@echo off
chcp 65001 >nul
title Cloudflare Quick Tunnel - Hệ Thống Thi Trực Tuyến 2026
echo ========================================================
echo   CLOUDFLARE TUNNEL - PHÁT LINK CHO HỌC SINH THI
echo ========================================================
echo.

REM 1. Kiểm tra và tự động tải cloudflared.exe nếu chưa có
if not exist "cloudflared.exe" (
    echo [1/2] Đang tự động tải công cụ cloudflared chính thức (khoảng 20MB)...
    powershell -Command "Invoke-WebRequest -Uri 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile 'cloudflared.exe'"
    if exist "cloudflared.exe" (
        echo [OK] Tải cloudflared.exe thành công!
    ) else (
        echo [LOI] Không thể tải. Vui lòng kiểm tra lại kết nối mạng!
        pause
        exit /b 1
    )
)

REM 2. Chạy đường hầm kết nối localhost:8000
echo.
echo [2/2] Đang kết nối máy tính của bạn ra Internet...
echo.
echo ========================================================
echo  * QUAN TRỌNG: Bạn cần chạy song song file "run.bat" trước!
echo  * Hãy tìm dòng có dạng: 
echo      https://xxxx-xxxx-xxxx.trycloudflare.com
echo  * Copy link đó gửi cho học sinh là làm bài được ngay!
echo  * Giữ cửa sổ này mở trong suốt thời gian học sinh thi.
echo ========================================================
echo.

cloudflared.exe tunnel --url http://localhost:8000

pause
