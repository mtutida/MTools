@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem MTools CompactMe Windows build script.
rem Priority order for Python:
rem   1) COMPACTME_PYTHON, if defined
rem   2) COMPRIMIDIA_PYTHON legacy alias, if defined
rem   3) MEDIACOMPRESSOR_PYTHON legacy alias, if defined
rem   4) the currently activated virtual environment, VIRTUAL_ENV
rem   5) local .venv next to this package
rem   6) parent .venv next to the source folder
rem   7) python from PATH

set "PY_CMD="

if defined COMPACTME_PYTHON (
  if exist "%COMPACTME_PYTHON%" set "PY_CMD=%COMPACTME_PYTHON%"
)

if not defined PY_CMD if defined COMPRIMIDIA_PYTHON (
  if exist "%COMPRIMIDIA_PYTHON%" set "PY_CMD=%COMPRIMIDIA_PYTHON%"
)

if not defined PY_CMD if defined MEDIACOMPRESSOR_PYTHON (
  if exist "%MEDIACOMPRESSOR_PYTHON%" set "PY_CMD=%MEDIACOMPRESSOR_PYTHON%"
)

if not defined PY_CMD if defined VIRTUAL_ENV (
  if exist "%VIRTUAL_ENV%\Scripts\python.exe" set "PY_CMD=%VIRTUAL_ENV%\Scripts\python.exe"
)

if not defined PY_CMD if exist "%~dp0.venv\Scripts\python.exe" (
  set "PY_CMD=%~dp0.venv\Scripts\python.exe"
)

if not defined PY_CMD if exist "%~dp0..\.venv\Scripts\python.exe" (
  set "PY_CMD=%~dp0..\.venv\Scripts\python.exe"
)

if not defined PY_CMD (
  where python >nul 2>nul
  if not errorlevel 1 set "PY_CMD=python"
)

if not defined PY_CMD (
  where py >nul 2>nul
  if not errorlevel 1 set "PY_CMD=py -3"
)

if not defined PY_CMD (
  echo ERROR: Python was not found.
  echo Activate the workspace .venv or define COMPACTME_PYTHON with the full python.exe path.
  exit /b 1
)

echo [CompactMe] Windows build started.
echo [CompactMe] Python command: %PY_CMD%
%PY_CMD% -c "import sys; print('[CompactMe] Python executable:', sys.executable); print('[CompactMe] Python version:', sys.version.split()[0])"
if errorlevel 1 exit /b 1

%PY_CMD% -m pip --version >nul 2>nul
if errorlevel 1 (
  echo ERROR: pip is not available for the selected Python.
  exit /b 1
)

%PY_CMD% -c "import PySide6; from PySide6.QtWidgets import QApplication; print('[CompactMe] PySide6:', PySide6.__version__)" >nul 2>nul
if errorlevel 1 (
  echo.
  echo ERROR: PySide6 is not available in the selected Python environment.
  echo The build was stopped to avoid generating a broken EXE with: No module named PySide6.
  echo.
  echo Use the same virtual environment used during development, for example:
  echo   .venv\Scripts\activate
  echo   build_windows.bat
  echo.
  echo Or explicitly set:
  echo   set COMPACTME_PYTHON=C:\path\to\MTCompactMe\.venv\Scripts\python.exe
  echo   build_windows.bat
  echo.
  echo Legacy variable still supported:
  echo   set COMPRIMIDIA_PYTHON=C:\path\to\python.exe
  echo   set MEDIACOMPRESSOR_PYTHON=C:\path\to\python.exe
  echo   build_windows.bat
  echo.
  exit /b 1
)
%PY_CMD% -c "import PySide6; print('[CompactMe] PySide6 version:', PySide6.__version__)"

%PY_CMD% -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
  echo PyInstaller was not found in the selected Python environment. Installing build dependencies...
  call :install_normal
  if errorlevel 1 (
    echo.
    echo ERROR: Could not install build dependencies using normal HTTPS validation.
    echo This is usually caused by a local Windows/Python certificate problem.
    echo.
    echo Safer fixes to try first:
    echo   1. Update Python from python.org.
    echo   2. Run: %PY_CMD% -m pip install --upgrade pip certifi
    echo   3. Run this script again.
    echo.
    echo If your network/proxy blocks certificate validation and you accept the risk,
    echo run this explicit fallback script:
    echo   install_build_deps_trusted_host.bat
    echo Then run:
    echo   build_windows.bat
    echo.
    exit /b 1
  )
)
%PY_CMD% -m PyInstaller --version

if not exist app\ui\icons\compactme.ico (
  echo ERROR: app\ui\icons\compactme.ico not found.
  echo The app icon asset is required for the compiled EXE and installer.
  exit /b 1
)

if not exist config.json (
  echo ERROR: config.json not found.
  exit /b 1
)

if exist validate_ffmpeg_binaries.bat (
  call validate_ffmpeg_binaries.bat
  if errorlevel 1 exit /b %errorlevel%
) else (
  echo ERROR: validate_ffmpeg_binaries.bat not found.
  echo The build was stopped to avoid generating a package without validated media tools.
  exit /b 1
)

rem FFmpeg capability validation remains available as a manual/optional audit,
rem but it is no longer a mandatory build gate. Some trusted FFmpeg builds
rem return non-zero exit codes for informational commands on Windows, which
rem can block valid builds even when the app itself works.
rem To run this stricter check intentionally, set:
rem   set COMPACTME_RUN_FFMPEG_CAPABILITY_CHECK=1
set "RUN_FFMPEG_CAPABILITY_CHECK=%COMPACTME_RUN_FFMPEG_CAPABILITY_CHECK%"
if not defined RUN_FFMPEG_CAPABILITY_CHECK set "RUN_FFMPEG_CAPABILITY_CHECK=%COMPRIMIDIA_RUN_FFMPEG_CAPABILITY_CHECK%"
if "%RUN_FFMPEG_CAPABILITY_CHECK%"=="1" (
  if exist validate_ffmpeg_capabilities.bat (
    call validate_ffmpeg_capabilities.bat
    if errorlevel 1 exit /b %errorlevel%
  ) else (
    echo ERROR: validate_ffmpeg_capabilities.bat not found.
    exit /b 1
  )
) else (
  echo [CompactMe] FFmpeg capability validation skipped during normal build.
  echo [CompactMe] To run it manually: validate_ffmpeg_capabilities.bat
)

%PY_CMD% -c "from app.core.app_version import APP_VERSION; print('[CompactMe] App version:', APP_VERSION)"
if errorlevel 1 (
  echo ERROR: Could not read app version from app\core\app_version.py.
  exit /b 1
)

%PY_CMD% -m compileall -q app
if errorlevel 1 exit /b 1

%PY_CMD% -m PyInstaller --clean --noconfirm CompactMe.spec
if errorlevel 1 exit /b 1

if not exist dist\CompactMe\CompactMe.exe (
  echo ERROR: Build finished without the expected dist\CompactMe\CompactMe.exe file.
  exit /b 1
)

echo.
echo [CompactMe] Build finished.
echo Output folder: dist\CompactMe
echo Executable:    dist\CompactMe\CompactMe.exe
echo.
echo Keep the whole dist\CompactMe folder when testing/distributing the onedir build.
endlocal
exit /b 0

:install_normal
%PY_CMD% -m pip install -r requirements-build.txt
exit /b %errorlevel%
