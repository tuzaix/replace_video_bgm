@echo off
REM Build Windows .exe for Video Concat GUI using PyInstaller
REM Prerequisites: Python, pip install -r requirements_gui.txt

setlocal
set PROJECT_ROOT=%~dp0
cd /d %PROJECT_ROOT%

if not exist .venv (
  echo Creating virtual environment...
  python -m venv .venv
)
call .venv\Scripts\activate.bat

pip install --upgrade pip
pip install -r requirements_gui.txt

REM Clean previous build artifacts to ensure a fresh build
echo Cleaning previous build artifacts...
echo Attempting to close running VideoConcatGUI.exe (if any)...
taskkill /IM VideoConcatGUI.exe /F >nul 2>nul
timeout /t 1 /nobreak >nul
if exist build (
  echo Removing build\
  rd /s /q build
)
if exist dist (
  echo Removing dist\
  rd /s /q dist
)
if exist VideoConcatGUI.spec (
  echo Removing VideoConcatGUI.spec
  del /q VideoConcatGUI.spec
)

REM Ensure PyInstaller runs from project root
REM Bundle built-in FFmpeg (place binaries under vendor\ffmpeg\bin)
REM The following adds the entire bin directory to the app under ffmpeg\bin
REM Conditionally include bundled FFmpeg if present
set ADD_DATA=
if exist vendor\ffmpeg\bin (
  echo Found vendor\ffmpeg\bin. Bundling FFmpeg binaries...
  set ADD_DATA=--add-data "vendor\ffmpeg\bin;ffmpeg\bin"
) else (
  echo WARNING: vendor\ffmpeg\bin not found. Building without bundled FFmpeg.
)

REM Add project root to analysis paths so concat_tool can be discovered
set PYI_PATHS=--paths "%PROJECT_ROOT%"

REM Normalize project root to avoid trailing backslash issues
set PROJECT_ROOT_NO_TRAILING=%PROJECT_ROOT:~0,-1%
set PYI_PATHS=--paths "%PROJECT_ROOT_NO_TRAILING%"

REM Show the exact command being executed for diagnostics
set BUILD_CMD=python -m PyInstaller -F -w -n VideoConcatGUI %ADD_DATA% %PYI_PATHS% --hidden-import concat_tool.video_concat --collect-all concat_tool "gui\main_gui.py"
echo Running: %BUILD_CMD%
%BUILD_CMD%

echo.
echo Build complete. Executable is in the dist\ folder.
echo.
pause