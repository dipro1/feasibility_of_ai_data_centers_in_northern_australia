@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_all.ps1"
if errorlevel 1 (
    echo.
    echo Simulation failed. Review the error shown above.
    pause
    exit /b 1
)
echo.
echo All simulations completed successfully.
pause
