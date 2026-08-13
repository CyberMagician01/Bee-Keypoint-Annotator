@echo off
chcp 65001 >nul
cd /d "%~dp0"
python app.py
if errorlevel 1 (
  echo.
  echo 启动失败，请确认 Python、Tkinter 和 Pillow 已安装。
  pause
)
