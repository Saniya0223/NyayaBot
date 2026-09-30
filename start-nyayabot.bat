@echo off
REM Single entry point for local NyayaBot development.
REM
REM Wraps start-nyayabot.ps1 so it runs by double-click or from any shell
REM without changing the machine's PowerShell execution policy. -ExecutionPolicy
REM Bypass applies to this one process only; nothing is changed permanently.
REM
REM   start-nyayabot          start backend + frontend in two terminals
REM   start-nyayabot -Force   stop whatever holds port 8000/3000, then start

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-nyayabot.ps1" %*
