<#
.SYNOPSIS
  Launches installed Google Chrome with remote debugging on port 9222.
  Zero profile data modification. Preserves all cookies and active logins.
#>

Write-Host "`n===================================================================" -ForegroundColor Cyan
Write-Host "  LinkedIn Activity Analyzer - Chrome Remote Debugging Launcher" -ForegroundColor Cyan
Write-Host "===================================================================`n" -ForegroundColor Cyan

$chromeExe = "C:\Program Files\Google\Chrome\Application\chrome.exe"
if (-not (Test-Path $chromeExe)) {
    $chromeExe = "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
}

if (-not (Test-Path $chromeExe)) {
    Write-Host "[ERROR] Google Chrome executable was not found." -ForegroundColor Red
    exit 1
}

$chromeProcesses = Get-Process -Name chrome -ErrorAction SilentlyContinue
if ($chromeProcesses) {
    Write-Host "[ACTION REQUIRED]" -ForegroundColor Yellow
    Write-Host "Google Chrome is currently running without remote debugging." -ForegroundColor Yellow
    Write-Host "Please close all open Google Chrome windows, then run this script again.`n" -ForegroundColor Yellow
    exit 1
}

Write-Host "Launching Google Chrome with remote debugging on port 9222..." -ForegroundColor Green
Start-Process -FilePath $chromeExe -ArgumentList "--remote-debugging-port=9222"

Start-Sleep -Seconds 2
try {
    $resp = Invoke-RestMethod -Uri "http://localhost:9222/json/version" -TimeoutSec 3
    Write-Host "`n[SUCCESS] Google Chrome is active on CDP port 9222!" -ForegroundColor Green
    Write-Host "  Browser: $($resp.Browser)" -ForegroundColor Gray
    Write-Host "Playwright will now automatically connect to your existing session.`n" -ForegroundColor Green
} catch {
    Write-Host "`n[NOTE] Chrome started. Please ensure port 9222 is accessible.`n" -ForegroundColor Gray
}
