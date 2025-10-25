@echo off
chcp 65001 >nul
echo ========================================
echo 视频BGM替换工具
echo ========================================
echo.

REM 检查Python是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo 错误: 未找到Python，请先安装Python 3.8+
    pause
    exit /b 1
)

REM 检查是否安装了依赖
echo 检查依赖库...
python -c "import torch, demucs, moviepy" >nul 2>&1
if errorlevel 1 (
    echo 正在安装依赖库...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo 依赖安装失败，请检查网络连接
        pause
        exit /b 1
    )
)

echo 依赖检查完成
echo.

REM 询问用户是否创建示例目录
set /p create_dirs="是否创建示例目录? (y/n): "
if /i "%create_dirs%"=="y" (
    python example.py --create-dirs
    echo.
    echo 请将视频文件放入 sample_videos 目录
    echo 请将音频文件放入 sample_bgm 目录
    echo 然后重新运行此脚本
    pause
    exit /b 0
)

REM 运行示例
echo 开始处理视频...
python example.py

echo.
echo 处理完成！按任意键退出...
pause