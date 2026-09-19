@echo off
title Hangzhou Mahjong - Web Battle Client
cd /d "%~dp0.."
echo 正在启动本地对战客户端(浏览器)…
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0web_client.ps1"
echo.
echo clientd 服务仍在后台运行。关闭此窗口不会停止服务。
echo 清理:Stop-Process 里对应端口的 clientd,或重启后本脚本会自动复用。
pause