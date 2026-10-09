@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "COMPACTME_EMBED_FFMPEG=0"
call build_windows.bat
endlocal
exit /b %errorlevel%
