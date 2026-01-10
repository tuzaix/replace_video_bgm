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

if not exist .venv313 (
  echo Creating virtual environment...
  python -m venv .venv313
)
set VENV_PYTHON=.\.venv313\Scripts\python.exe

%VENV_PYTHON% -m pip install --upgrade pip
%VENV_PYTHON% -m pip install -r requirements.txt || echo WARNING: Failed to install some requirements, proceeding anyway.

REM Optional: install PyArmor for code obfuscation
set OBFUSCATE=1
set OBF_DIR=build\obf
if "%OBFUSCATE%"=="1" (
  echo Installing PyArmor for obfuscation...
  %VENV_PYTHON% -m pip install -q pyarmor || echo WARNING: Failed to install PyArmor, will build without obfuscation.
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
if exist release (
  echo Removing release\
  rd /s /q release
)
if exist VideoConcatGUI.spec (
  echo Removing VideoConcatGUI.spec
  del /q VideoConcatGUI.spec
)

REM Ensure PyInstaller runs from project root
REM Bundling: We will copy third-party binaries (FFmpeg) to the same directory as EXE for portability
REM as requested by user. We no longer use --add-data for FFmpeg to keep it outside.
set ADD_DATA=

REM Include GUI resources: pack gui\wechat directory and ONLY gui\secretkey\public.pem
REM NOTE: Do NOT include private.pem for security reasons.
set ADD_DATA=%ADD_DATA% --add-data "gui\wechat;gui\wechat"
REM IMPORTANT: For a single file, DEST must be the target directory, not including the filename.
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
  REM Try PyArmor v8+ command first
  %VENV_PYTHON% -m pyarmor.cli gen -O "%OBF_DIR%" -r gui concat_tool cover_tool video_tool merge_bgm_tool utils %ENTRY_SCRIPT%
  if errorlevel 1 (
    echo PyArmor v8+ 'gen' failed, trying legacy 'obfuscate'...
    %VENV_PYTHON% -m pyarmor.cli obfuscate -r -O "%OBF_DIR%" gui concat_tool cover_tool video_tool merge_bgm_tool utils %ENTRY_SCRIPT%
  )
  if exist "%OBF_DIR%\gui\main_gui.py" (
    set ENTRY_SCRIPT="%OBF_DIR%\gui\main_gui.py"
    REM Prefer obfuscated sources only to avoid bundling original code
    set PYI_PATHS=%PYI_PATHS% --paths "%OBF_DIR%"
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
REM We must also force include major libraries because PyArmor hides imports from PyInstaller
set HID_IMPORTS=--collect-all PySide6 --collect-all librosa --collect-all scipy --collect-all numpy --collect-all cv2 --collect-all gui --collect-all concat_tool --collect-all cover_tool --collect-all video_tool --collect-all merge_bgm_tool --collect-all utils --collect-all wmi --hidden-import pythoncom --hidden-import pywintypes --collect-all pycryptodome --hidden-import moviepy --hidden-import moviepy.editor --hidden-import moviepy.config --hidden-import moviepy.tools --hidden-import moviepy.video.fx.all --hidden-import moviepy.audio.fx.all --collect-all moviepy --collect-all imageio --collect-all imageio_ffmpeg --collect-all decorator --collect-all proglog --collect-all requests --collect-all tqdm --collect-all torch --collect-all torchaudio --collect-all ultralytics --collect-all faster_whisper --collect-all soundfile --collect-all pydub --collect-all PIL
if exist "%OBF_DIR%\gui\main_gui.py" (
  set HID_IMPORTS=%HID_IMPORTS% --hidden-import pytransform --hidden-import pyarmor_runtime --collect-all pyarmor_runtime
)
set BUILD_CMD=%VENV_PYTHON% -m PyInstaller -F -w -n VideoConcatGUI --runtime-tmpdir "%RUNTIME_TMPDIR%" %PYI_UPX% %ADD_DATA% %PYI_PATHS% --hidden-import concat_tool.video_concat --collect-submodules concat_tool --collect-submodules gui --collect-submodules cover_tool --collect-submodules video_tool --collect-submodules merge_bgm_tool --collect-submodules utils %HID_IMPORTS% %ENTRY_SCRIPT%
echo Running: %BUILD_CMD%
%BUILD_CMD%

echo.
echo Build complete. Executables are in the dist\ folder:
echo   - Stable (windowed):   dist\VideoConcatGUI.exe
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
  ) else (
    echo WARNING: ENABLE_SIGN=1 but CERT_FILE not set, skipping code signing.
  )
)

REM Rename executables to requested APP_NAME and prepare portable directory
set "APP_NAME=短视频工具v1.0"
set "PORTABLE_DIR=dist\%APP_NAME%"

if exist "%PORTABLE_DIR%" rd /s /q "%PORTABLE_DIR%"
mkdir "%PORTABLE_DIR%"

echo Preparing portable distribution in %PORTABLE_DIR%...
if exist "dist\VideoConcatGUI.exe" (
    copy "dist\VideoConcatGUI.exe" "%PORTABLE_DIR%\%APP_NAME%.exe"
)

REM Copy third-party tools (FFmpeg) to the portable directory
if exist "vendor\ffmpeg" (
    echo Copying FFmpeg to portable directory...
    xcopy /E /I /Y "vendor\ffmpeg" "%PORTABLE_DIR%\ffmpeg"
) else (
    echo WARNING: vendor\ffmpeg not found, FFmpeg will not be included in portable ZIP.
)

REM Package release zip using tar (standard on Win10/11) to avoid PowerShell's 2GB limit
set "RELEASE_DIR=release"
if not exist "%RELEASE_DIR%" mkdir "%RELEASE_DIR%"
set "ZIP_PATH=%RELEASE_DIR%\%APP_NAME%.zip"

echo Zipping %PORTABLE_DIR% to %ZIP_PATH%...
tar -a -c -f "%ZIP_PATH%" -C dist "%APP_NAME%"
if exist "%ZIP_PATH%" (
    echo Created release package: %ZIP_PATH%
) else (
    echo WARNING: Failed to create release zip.
)