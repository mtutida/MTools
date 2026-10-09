@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo [CompactMe] NSIS installer build started.

if not exist "dist\CompactMe\CompactMe.exe" (
    echo.
    echo ERROR: dist\CompactMe\CompactMe.exe was not found.
    echo Run build_windows.bat first and validate the generated app folder.
    exit /b 1
)

set "APP_VERSION="
for /f "tokens=3 delims= " %%V in ('findstr /B /C:"APP_VERSION = " "app\core\app_version.py"') do set "APP_VERSION=%%~V"

set "NSI_VERSION="
for /f "tokens=3 delims= " %%V in ('findstr /B /C:"!define APP_VERSION " "CompactMe.nsi"') do set "NSI_VERSION=%%~V"

if not defined APP_VERSION (
    echo.
    echo ERROR: Could not read APP_VERSION from app\core\app_version.py.
    exit /b 1
)

if not defined NSI_VERSION (
    echo.
    echo ERROR: Could not read APP_VERSION from CompactMe.nsi.
    exit /b 1
)

if not "%APP_VERSION%"=="%NSI_VERSION%" (
    echo.
    echo ERROR: app\core\app_version.py is not synchronized with CompactMe.nsi.
    echo app\core\app_version.py: %APP_VERSION%
    echo CompactMe.nsi:        %NSI_VERSION%
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "if ((Get-Item 'dist\CompactMe\CompactMe.exe').LastWriteTime -lt (Get-Item 'app\core\app_version.py').LastWriteTime) { exit 1 }"
if errorlevel 1 (
    echo.
    echo ERROR: dist\CompactMe\CompactMe.exe appears older than the current source version.
    echo Run clean_build.bat and build_windows.bat before generating the installer.
    exit /b 1
)

set "MAKENSIS="
for /f "delims=" %%I in ('where makensis.exe 2^>nul') do (
    if not defined MAKENSIS set "MAKENSIS=%%I"
)

if not defined MAKENSIS (
    if exist "%ProgramFiles(x86)%\NSIS\makensis.exe" set "MAKENSIS=%ProgramFiles(x86)%\NSIS\makensis.exe"
)

if not defined MAKENSIS (
    echo.
    echo ERROR: makensis.exe was not found.
    echo Install NSIS or add its folder to PATH, then run this script again.
    echo Common path: C:\Program Files ^(x86^)\NSIS\makensis.exe
    exit /b 1
)

echo Using app version: %APP_VERSION%
echo Using NSIS compiler: %MAKENSIS%

if not exist "installer_output" mkdir "installer_output"
"%MAKENSIS%" /INPUTCHARSET UTF8 "CompactMe.nsi"
if errorlevel 1 (
    echo.
    echo ERROR: NSIS installer build failed.
    exit /b 1
)

echo.
echo [CompactMe] NSIS installer build finished.
echo Output folder: installer_output
echo Installer:     installer_output\MTools_CompactMe_Setup_%APP_VERSION%.exe
endlocal
