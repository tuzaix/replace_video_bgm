@echo off
chcp 65001 >nul
echo ========================================
echo 视频拼接工具 - 使用示例
echo ========================================
echo.

REM 设置示例路径（请根据实际情况修改）
set VIDEO_DIR=D:\Videos
set BGM_FILE=D:\Music\background.mp3
REM 输出将自动创建在 D:\Videos_longvideo\ 目录中

echo 当前配置:
echo 视频目录: %VIDEO_DIR%
echo BGM文件: %BGM_FILE%
echo 输出目录: %VIDEO_DIR%_longvideo\
echo.

echo 请确保以上路径正确，然后按任意键继续...
pause >nul

echo.
echo 开始处理...
echo.

REM 执行视频拼接命令（输出目录将自动创建）
python video_concat.py "%VIDEO_DIR%" "%BGM_FILE%" -n 5

echo.
echo 处理完成！
echo.
pause