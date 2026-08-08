@echo off
cd /d "%~dp0"
echo Preparing VideoCraft Studio...
python -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo.
  echo Setup could not finish. Check Python and your connection, then try again.
  pause
  exit /b 1
)
python -c "import sys; from pathlib import Path; Path('python_path.txt').write_text(str(Path(sys.executable).with_name('pythonw.exe')), encoding='utf-8')"
echo.
echo Ready. Open VideoCraftStudio.exe to start.
pause
