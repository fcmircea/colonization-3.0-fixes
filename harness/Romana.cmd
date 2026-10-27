@echo off
rem Start the Romanian build (MPS\ROMANA) in DOSBox-Daum.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Play.ps1" ROMANA %*
if errorlevel 1 pause
