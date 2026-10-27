@echo off
rem Start the English game (MPS\COLONIZE) in DOSBox-Daum.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Play.ps1" COLONIZE %*
if errorlevel 1 pause
