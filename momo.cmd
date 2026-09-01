@echo off
title MOMO Agent
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
set "MOMO_EXIT_CODE=%ERRORLEVEL%"
echo.
echo [MOMO] Service stopped (exit code %MOMO_EXIT_CODE%).
pause
exit /b %MOMO_EXIT_CODE%
