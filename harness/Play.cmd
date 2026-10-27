@echo off
rem Start Colonization with the settings from Setup.cmd (default language).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Play.ps1" %*
if errorlevel 1 pause
