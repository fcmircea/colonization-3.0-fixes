@echo off
rem Install the Colonization 3.0 fixes (Install.ps1): asks for your game folder.
set "ps1=%~dp0install\Install.ps1"
if not exist "%ps1%" set "ps1=%~dp0Install.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%ps1%" %*
if errorlevel 1 pause
