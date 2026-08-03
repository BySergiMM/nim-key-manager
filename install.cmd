@echo off
:: NIM Key Manager installer for cmd.exe (double-click friendly).
:: Delegates to install.ps1 next to this file, or downloads it when run alone.
setlocal

set "SCRIPT=%~dp0install.ps1"
if exist "%SCRIPT%" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; irm https://raw.githubusercontent.com/BySergiMM/nim-key-manager/main/install.ps1 | iex"
)

if errorlevel 1 (
    echo.
    echo Installation failed. See the output above.
    pause
    exit /b 1
)

echo.
echo Done. Open a new terminal and run:  nimkm up
pause
