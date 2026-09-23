@echo off
title Hangzhou Mahjong - Web Battle Client
cd /d "%~dp0.."
echo 正在启动本地对战客户端(浏览器)…
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0web_client.ps1"
echo.
echo web_client 退出时会停止本次启动的 clientd；下次运行会重启旧实例。
pause
