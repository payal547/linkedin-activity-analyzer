@echo off
echo ===================================================================
echo   LinkedIn Activity Analyzer - Chrome Remote Debugging Launcher
echo ===================================================================
echo.
echo This script launches your installed Google Chrome with remote
echo debugging enabled on port 9222.
echo.
echo SAFETY GUARANTEES:
echo  - Uses your normal Chrome User Data directory.
echo  - Preserves 100%% of your bookmarks, history, tabs, and logins.
echo  - Zero data loss, zero profile resets.
echo.
echo Checking if Chrome is currently running...
tasklist /FI "IMAGENAME eq chrome.exe" 2>NUL | find /I /N "chrome.exe">NUL
if "%ERRORLEVEL%"=="0" (
    echo.
    echo [ACTION REQUIRED]
    echo Google Chrome is currently open without remote debugging.
    echo Chromium requires remote debugging to be enabled at startup.
    echo.
    echo Please close all open Google Chrome windows, then run this
    echo script again.
    echo.
    pause
    exit /b 1
)

echo.
if not "%~1"=="" (
    echo Launching Google Chrome with remote debugging on port 9222 (Profile: %~1)...
    start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --profile-directory="%~1"
) else (
    echo Launching Google Chrome with remote debugging on port 9222...
    start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
)

echo.
echo [SUCCESS] Chrome has been launched on port 9222!
echo You can now run your LinkedIn analysis, and Playwright will
echo automatically reuse your existing authenticated session.
echo.
echo NOTE: If you use multiple Chrome profiles (e.g. Work / Personal),
echo you can launch with a specific profile by passing its directory, e.g.:
echo   launch_chrome_with_debug.bat "Profile 2"
echo.
pause
