@echo off
REM Example script to run the concat_tool workflow via CLI.
REM Adjust the paths below before running.

setlocal
set PYTHON_EXE=python

REM Optional: allow system ffmpeg fallback in development only.
REM Uncomment the following line if bundled ffmpeg is not yet prepared.
REM set FFMPEG_DEV_FALLBACK=1

%PYTHON_EXE% -m concat_tool.cli ^
  --video-dirs "D:\\videos1" "D:\\videos2" ^
  --bgm-path "D:\\audios" ^
  --outputs 2 --count 5 --gpu --threads 4 ^
  --width 1080 --height 1920 --fps 25 --fill pad ^
  --trim-head 0.0 --trim-tail 1.0 --group-res --quality-profile balanced

endlocal