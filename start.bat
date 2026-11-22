@echo off
rem Start the Attendance System on this laptop. Double-click this file.
rem Keep this window open while using the system; close it (or Ctrl+C) to stop.
cd /d "%~dp0"
title Attendance System - running

if not exist "venv\Scripts\python.exe" (
  echo Run setup.bat first.
  pause
  exit /b 1
)

echo.
echo  Students:     http://localhost:5000
echo  Instructors:  http://prof.localhost:5000
echo  Phones on the same Wi-Fi: use this laptop's address shown below as "Running on http://192.168..."
echo  Close this window to stop the system.
echo.
venv\Scripts\python run.py
pause
