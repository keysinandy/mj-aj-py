@echo off
title Hangzhou Mahjong - Web Battle Client
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0web_client.ps1"
echo.
pause
