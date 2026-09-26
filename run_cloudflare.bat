@echo off
title Cloudflare Quick Tunnel - He Thong Thi Truc Tuyen 2026
cls
echo ========================================================
echo   CLOUDFLARE TUNNEL - PHAT LINK CHO HOC SINH THI
echo ========================================================
echo.

if not exist cloudflared.exe (
    echo [1/2] Dang tai cloudflared.exe tu dong...
    powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; (New-Object System.Net.WebClient).DownloadFile('https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe', 'cloudflared.exe')"
)

if not exist cloudflared.exe (
    echo [LOI] Khong the tai cloudflared.exe. Vui long kiem tra lai mang!
    pause
    exit /b 1
)

echo [OK] Da san sang cong cu Cloudflare!
echo.
echo ========================================================
echo  * LUU Y: Ban can chay song song file "run.bat" truoc!
echo  * Doi vai giay, he thong se hien thi duong link co dang:
echo.
echo      https://...trycloudflare.com
echo.
echo  * Copy duong link tren gui cho hoc sinh lam bai ngay!
echo  * Luu y: Giu cua so nay mo trong suot thoi gian thi.
echo ========================================================
echo.

cloudflared.exe tunnel --url http://localhost:8000

pause
