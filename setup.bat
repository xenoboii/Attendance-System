@echo off
rem One-time setup on a new Windows laptop. Double-click this file.
rem Makes the Python environment, installs the packages and loads demo data.
cd /d "%~dp0"
title Attendance System - setup

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo.
  echo Python is not installed.
  echo Install Python 3.12 or newer from https://www.python.org/downloads/
  echo IMPORTANT: on the first installer screen tick "Add python.exe to PATH".
  echo Then double-click setup.bat again.
  pause
  exit /b 1
)

echo == Python version
%PY% --version

if not exist "venv\Scripts\python.exe" (
  echo == Creating the Python environment: venv
  %PY% -m venv venv || (echo Could not create venv. & pause & exit /b 1)
)

echo == Installing packages (needs internet, about 1-2 minutes)
venv\Scripts\python -m pip install --upgrade pip
venv\Scripts\python -m pip install -r requirements.txt || (echo Package install failed - check the internet connection and run setup.bat again. & pause & exit /b 1)

if not exist "data\attendance.db" (
  echo == Loading demo data - the accounts are listed in app\seed.py
  venv\Scripts\flask --app run seed-demo
) else (
  echo == Keeping the existing database in data\attendance.db
)

echo.
echo Setup finished. Double-click start.bat to run the system.
pause
