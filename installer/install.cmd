@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set "W18_EXIT=%ERRORLEVEL%"
endlocal & exit /b %W18_EXIT%
