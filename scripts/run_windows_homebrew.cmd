@echo off
setlocal
set "HOMEBREW_PYTHON=%~dp0..\.venv-windows\Scripts\python.exe"
if not exist "%HOMEBREW_PYTHON%" (
    echo [RED] Local Python environment missing. Follow docs/windows-homebrew.md setup.
    if "%~1"=="" pause
    exit /b 1
)
"%HOMEBREW_PYTHON%" "%~dp0run_windows_homebrew.py" %*
set "HOMEBREW_STATUS=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %HOMEBREW_STATUS%
