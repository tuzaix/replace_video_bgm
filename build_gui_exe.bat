@echo off
REM Ensure console uses UTF-8 to avoid mojibake when echoing Unicode
chcp 65001 >nul
REM Build Windows .exe for Video Concat GUI using PyInstaller
REM Prerequisites: Python, pip install -r requirements_gui.txt

setlocal
REM Security hardening options
REM Encryption via --key is not supported in PyInstaller >= 6.0; rely on PyArmor obfuscation
set PROJECT_ROOT=%~dp0
cd /d %PROJECT_ROOT%

if not exist .venv (
  echo Creating virtual environment...
  python -m venv .venv
)
call .venv\Scripts\activate.bat

pip install --upgrade pip
pip install -r requirements_gui.txt

REM Optional: install PyArmor for code obfuscation
set OBFUSCATE=1
set OBF_DIR=build\obf
if "%OBFUSCATE%"=="1" (
  echo Installing PyArmor for obfuscation...
  pip install -q pyarmor || echo WARNING: Failed to install PyArmor, will build without obfuscation.
)

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

REM Include GUI resources: pack gui\wechat directory and ONLY gui\secretkey\public.pem
REM NOTE: Do NOT include private.pem for security reasons.
set ADD_DATA=%ADD_DATA% --add-data "gui\wechat;gui\wechat"
REM IMPORTANT: For a single file, DEST must be the target directory, not including the filename.
REM Otherwise PyInstaller will create a nested folder named 'public.pem' and place the file inside it.
set ADD_DATA=%ADD_DATA% --add-data "gui\secretkey\public.pem;gui\secretkey"

REM Add project root to analysis paths so concat_tool can be discovered
set PYI_PATHS=--paths "%PROJECT_ROOT%"

REM Normalize project root to avoid trailing backslash issues
set PROJECT_ROOT_NO_TRAILING=%PROJECT_ROOT:~0,-1%
set PYI_PATHS=--paths "%PROJECT_ROOT_NO_TRAILING%"

REM Perform code obfuscation (PyArmor) to increase reverse-engineering difficulty
set ENTRY_SCRIPT="gui\main_gui.py"
if "%OBFUSCATE%"=="1" (
  echo Obfuscating Python sources with PyArmor...
  if not exist "%OBF_DIR%" (
    mkdir "%OBF_DIR%"
  )
  REM Try PyArmor v8 command first
  pyarmor gen -O "%OBF_DIR%" -r gui concat_tool %ENTRY_SCRIPT%
  if errorlevel 1 (
    echo PyArmor v8 'gen' failed, trying legacy 'obfuscate'...
    pyarmor obfuscate -r -O "%OBF_DIR%" gui concat_tool %ENTRY_SCRIPT%
  )
  if exist "%OBF_DIR%\gui\main_gui.py" (
    set ENTRY_SCRIPT="%OBF_DIR%\gui\main_gui.py"
    REM Prefer obfuscated sources only to avoid bundling original code
    set PYI_PATHS=--paths "%OBF_DIR%"
    echo Using obfuscated entry script: %ENTRY_SCRIPT%
  ) else (
    echo WARNING: Obfuscation output not found, building without obfuscation.
  )
)

REM Control where onefile runtime extracts its temporary files.
REM Using current directory (.) helps keep relative resource paths consistent with dev layout.
REM You can change this to %TEMP% if you deploy to a non-writable directory.
set RUNTIME_TMPDIR=.

REM Optional: Python optimization (strip docstrings in stable build)
set STABLE_PYOPT=2
set DEBUG_PYOPT=0

REM Optional: UPX compression
set USE_UPX=0
set UPX_DIR=%PROJECT_ROOT%vendor\upx
set PYI_UPX=
if "%USE_UPX%"=="1" (
  if exist "%UPX_DIR%" (
    set PYI_UPX=--upx-dir "%UPX_DIR%"
    echo Using UPX from %UPX_DIR%
  ) else (
    echo WARNING: UPX directory not found, skipping UPX compression.
  )
)

REM Show the exact command being executed for diagnostics
REM Security: use --collect-submodules for concat_tool to avoid bundling .py sources as data.
set PYTHONOPTIMIZE=%STABLE_PYOPT%
REM Hidden imports for PyArmor should only be included when obfuscation actually succeeded
set HID_IMPORTS=
if exist "%OBF_DIR%\gui\main_gui.py" (
  set HID_IMPORTS=--hidden-import pytransform --hidden-import pyarmor_runtime --collect-all pyarmor_runtime
)
set BUILD_CMD=python -m PyInstaller -F -w -n VideoConcatGUI --runtime-tmpdir "%RUNTIME_TMPDIR%" %PYI_UPX% %ADD_DATA% %PYI_PATHS% --hidden-import concat_tool.video_concat --collect-submodules concat_tool %HID_IMPORTS% %ENTRY_SCRIPT%
echo Running: %BUILD_CMD%
%BUILD_CMD%

echo.
echo Building debug variant with console...
set PYTHONOPTIMIZE=%DEBUG_PYOPT%
set BUILD_CMD_DEBUG=python -m PyInstaller -F -n VideoConcatGUI_debug --runtime-tmpdir "%RUNTIME_TMPDIR%" %PYI_UPX% %ADD_DATA% %PYI_PATHS% --hidden-import concat_tool.video_concat --collect-submodules concat_tool %HID_IMPORTS% %ENTRY_SCRIPT%
echo Running: %BUILD_CMD_DEBUG%
%BUILD_CMD_DEBUG%

echo.
echo Build complete. Executables are in the dist\ folder:
echo   - Stable (windowed):   dist\VideoConcatGUI.exe
echo   - Debug  (console):    dist\VideoConcatGUI_debug.exe
echo.

REM Optional: Code signing (requires signtool and certificate)
set ENABLE_SIGN=0
set CERT_FILE=
set CERT_PASS=
if "%ENABLE_SIGN%"=="1" (
  if defined CERT_FILE (
    if exist "dist\VideoConcatGUI.exe" (
      echo Signing dist\VideoConcatGUI.exe...
      signtool sign /f "%CERT_FILE%" /p "%CERT_PASS%" /tr http://timestamp.digicert.com /td sha256 /fd sha256 "dist\VideoConcatGUI.exe"
    )
    if exist "dist\VideoConcatGUI_debug.exe" (
      echo Signing dist\VideoConcatGUI_debug.exe...
      signtool sign /f "%CERT_FILE%" /p "%CERT_PASS%" /tr http://timestamp.digicert.com /td sha256 /fd sha256 "dist\VideoConcatGUI_debug.exe"
    )
  ) else (
    echo WARNING: ENABLE_SIGN=1 but CERT_FILE not set, skipping code signing.
  )
)

REM Rename executables to requested APP_NAME using PowerShell to preserve Unicode correctly
set "APP_NAME=短视频搬运工具v1.0(NVIDIA GPU版本)"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $name=$env:APP_NAME; $dist='dist'; if (Test-Path (Join-Path $dist 'VideoConcatGUI.exe')) { Rename-Item -LiteralPath (Join-Path $dist 'VideoConcatGUI.exe') -NewName ($name + '.exe') } if (Test-Path (Join-Path $dist 'VideoConcatGUI_debug.exe')) { Rename-Item -LiteralPath (Join-Path $dist 'VideoConcatGUI_debug.exe') -NewName ($name + '_debug.exe') }"

REM Package release zip using PowerShell (built-in) instead of rar
set "RELEASE_DIR=release"

if not exist "%RELEASE_DIR%" mkdir "%RELEASE_DIR%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $name=$env:APP_NAME; $releaseDir='%RELEASE_DIR%'; $dst=Join-Path $releaseDir ($name + '.zip'); $src=Join-Path 'dist' ($name + '.exe'); Compress-Archive -Force -DestinationPath $dst -Path $src; if (Test-Path $dst) { Write-Host ('Created release package: ' + $dst) } else { Write-Warning ('Failed to create release zip: ' + $dst) }"