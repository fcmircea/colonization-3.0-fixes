@echo off
rem Choose emulator, language, CD music and volume, screen (Setup.ps1).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Setup.ps1" %*
if errorlevel 1 pause
