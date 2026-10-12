@echo off
chcp 65001 >nul
title 论文智能体 · 一键启动
cd /d "%~dp0"

echo ============================================
echo   论文智能体 一键启动
echo ============================================
echo.

REM ---- 1) 环境自检 ----
if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未找到虚拟环境 .venv
    echo        请先在项目目录创建：python -m venv .venv 并安装依赖
    pause
    exit /b 1
)
if not exist ".env" (
    echo [错误] 未找到 .env 文件（需要 ZHIPU_API_KEY=...）
    pause
    exit /b 1
)

REM ---- 2) 已在运行则直接打开浏览器 ----
netstat -ano | findstr ":8000" | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo [提示] 服务已在运行，直接打开页面…
    start "" "http://127.0.0.1:8000"
    ping -n 3 127.0.0.1 >nul
    exit /b 0
)

REM ---- 3) 加载 .env（KEY=VALUE 行）----
echo [1/3] 加载环境变量（.env）…
for /f "usebackq tokens=1,* delims==" %%a in (`findstr /r "^[A-Za-z_]" ".env"`) do set "%%a=%%b"

REM ---- 4) 后台启动服务（最小化窗口，日志写 server.log）----
echo [2/3] 启动服务（日志：server.log）…
start "论文智能体-服务" /min cmd /c ".venv\Scripts\python.exe -m src.web_api >> server.log 2>&1"

REM ---- 5) 健康检查通过后打开浏览器（最多等 40 秒）----
echo [3/3] 等待服务就绪…
set /a waited=0
:waitloop
ping -n 3 127.0.0.1 >nul
curl -s -o nul -m 2 http://127.0.0.1:8000/api/health 2>nul
if %errorlevel%==0 goto ready
set /a waited+=2
if %waited% geq 40 goto timeout_err
goto waitloop

:ready
echo.
echo ✅ 启动成功：http://127.0.0.1:8000  （正在打开浏览器）
start "" "http://127.0.0.1:8000"
echo.
echo 提示：服务在后台最小化窗口运行；停止服务可运行「停止论文智能体.bat」或关闭该窗口。
ping -n 6 127.0.0.1 >nul
exit /b 0

:timeout_err
echo.
echo [警告] 服务 40 秒内未就绪，请查看 server.log 排查（常见：ZHIPU_API_KEY 缺失 / 端口被占用）。
start "" notepad server.log
pause
exit /b 1
