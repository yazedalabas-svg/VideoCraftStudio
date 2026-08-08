@echo off
cd /d "%~dp0"
if exist "VideoCraftStudio.exe" (
  start "" "VideoCraftStudio.exe"
) else (
  start "" pythonw "app.py"
)
