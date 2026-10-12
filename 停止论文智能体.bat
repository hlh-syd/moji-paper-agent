@echo off
chcp 65001 >nul
title 论文智能体 · 停止服务
echo 正在停止论文智能体服务…
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do taskkill /PID %%p /F >nul 2>&1
netstat -ano | findstr ":8000" | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo [警告] 端口 8000 仍被占用，可能有其他进程，请手动检查。
) else (
    echo ✅ 服务已停止。
)
ping -n 4 127.0.0.1 >nul
exit /b 0
