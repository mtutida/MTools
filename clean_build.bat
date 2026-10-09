@echo off
setlocal
cd /d "%~dp0"
echo Removing build artifacts...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
for /d /r %%D in (__pycache__) do @if exist "%%D" rmdir /s /q "%%D"
for /r %%F in (*.pyc) do @if exist "%%F" del /q "%%F"
echo Done.
endlocal
