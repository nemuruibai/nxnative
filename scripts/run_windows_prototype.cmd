@echo off
setlocal
set "PROTOTYPE_PYTHON=%~dp0..\.venv-windows\Scripts\python.exe"
if not exist "%PROTOTYPE_PYTHON%" (
    echo [RED] Local Python environment missing. Follow docs/windows-prototype.md setup.
    if "%~1"=="" pause
    exit /b 1
)
"%PROTOTYPE_PYTHON%" "%~dp0run_windows_prototype.py" %*
set "PROTOTYPE_STATUS=%ERRORLEVEL%"
if "%~1"=="" if not "%PROTOTYPE_STATUS%"=="0" pause
exit /b %PROTOTYPE_STATUS%
