@echo off
REM 一键构建 Windows 免安装版（等价于 python build.py）
setlocal
cd /d "%~dp0"
python build.py %*
if errorlevel 1 (
    echo.
    echo 构建失败，请检查上方输出。
    pause
    exit /b 1
)
echo.
echo 构建完成，产物在 dist\ 目录。
pause