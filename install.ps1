<#
.SYNOPSIS
    NIM Key Manager installer for Windows.

.DESCRIPTION
    irm https://raw.githubusercontent.com/BySergiMM/nim-key-manager/main/install.ps1 | iex

    Downloads a self-contained runtime into a private directory, installs the
    newest release, writes a `nimkm` launcher, puts it on the user PATH, runs
    `nimkm init` (secrets, database, administrator) and offers to register the
    MCP server with the clients found on this machine.

    Nothing is installed machine-wide and no administrator rights are needed.

.PARAMETER Version
    Install a specific version instead of the latest release.

.PARAMETER InstallDir
    Where everything lives. Default: %LOCALAPPDATA%\nim-key-manager

.PARAMETER Source
    Install from a local checkout or an explicit pip requirement (used by CI).

.PARAMETER NoModifyPath
    Do not add the launcher directory to the user PATH.

.PARAMETER NoInit
    Install only; skip configuration.

.PARAMETER NoMcpSetup
    Do not offer to register the MCP server with local clients.
#>
[CmdletBinding()]
param(
    [string]$Version = $env:NIMKM_VERSION,
    [string]$InstallDir = $env:NIMKM_HOME,
    [string]$Source = $env:NIMKM_SOURCE,
    [switch]$NoModifyPath,
    [switch]$NoInit,
    [switch]$NoMcpSetup
)

$ErrorActionPreference = 'Stop'
# Windows PowerShell 5.1 defaults to TLS 1.0, which GitHub refuses.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is ~10x faster without it

$Repo = 'BySergiMM/nim-key-manager'
$Package = 'nim-key-manager'
$PythonVersion = if ($env:NIMKM_PYTHON_VERSION) { $env:NIMKM_PYTHON_VERSION } else { '3.12' }
$UvVersion = if ($env:NIMKM_UV_VERSION) { $env:NIMKM_UV_VERSION } else { 'latest' }

function Write-Step { param([string]$Message) Write-Host ":: " -ForegroundColor Cyan -NoNewline; Write-Host $Message }
function Write-Ok   { param([string]$Message) Write-Host "OK  " -ForegroundColor Green -NoNewline; Write-Host $Message }
function Write-Warn { param([string]$Message) Write-Host "!!  " -ForegroundColor Yellow -NoNewline; Write-Host $Message }
function Stop-WithError {
    param([string]$Message)
    Write-Host "XX  " -ForegroundColor Red -NoNewline
    Write-Host $Message
    exit 1
}

function Get-Target {
    $arch = $env:PROCESSOR_ARCHITECTURE
    if ($env:PROCESSOR_ARCHITEW6432) { $arch = $env:PROCESSOR_ARCHITEW6432 }
    switch ($arch) {
        'AMD64' { return 'x86_64-pc-windows-msvc' }
        'ARM64' { return 'aarch64-pc-windows-msvc' }
        'x86'   { return 'i686-pc-windows-msvc' }
        default { Stop-WithError "unsupported architecture: $arch" }
    }
}

function Install-Uv {
    param([string]$RuntimeDir)
    $uv = Join-Path $RuntimeDir 'bin\uv.exe'
    if (Test-Path $uv) { return $uv }

    $target = Get-Target
    if ($UvVersion -eq 'latest') {
        $url = "https://github.com/astral-sh/uv/releases/latest/download/uv-$target.zip"
    } else {
        $url = "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-$target.zip"
    }
    Write-Step "downloading the runtime for $target"
    $binDir = Join-Path $RuntimeDir 'bin'
    New-Item -ItemType Directory -Force -Path $binDir | Out-Null
    $temp = Join-Path ([IO.Path]::GetTempPath()) ("nimkm-uv-" + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $temp | Out-Null
    $archive = Join-Path $temp 'uv.zip'
    try {
        Invoke-WebRequest -Uri $url -OutFile $archive -UseBasicParsing
        Expand-Archive -Path $archive -DestinationPath $temp -Force
        $found = Get-ChildItem -Path $temp -Filter 'uv.exe' -Recurse | Select-Object -First 1
        if (-not $found) { Stop-WithError 'unexpected uv archive layout' }
        Move-Item -Path $found.FullName -Destination $uv -Force
    } catch {
        Stop-WithError "could not download uv: $($_.Exception.Message)"
    } finally {
        Remove-Item -Recurse -Force $temp -ErrorAction SilentlyContinue
    }
    return $uv
}

function Resolve-Source {
    if ($Source) { return $Source }
    if ($Version) {
        $api = "https://api.github.com/repos/$Repo/releases/tags/v$($Version.TrimStart('v'))"
    } else {
        $api = "https://api.github.com/repos/$Repo/releases/latest"
    }
    try {
        $release = Invoke-RestMethod -Uri $api -Headers @{ 'User-Agent' = 'nimkm-installer' }
        $wheel = $release.assets | Where-Object { $_.name -like '*.whl' } | Select-Object -First 1
        if ($wheel) { return $wheel.browser_download_url }
    } catch {
        Write-Verbose "no GitHub release found: $($_.Exception.Message)"
    }
    if ($Version) { return "$Package==$($Version.TrimStart('v'))" }
    try {
        Invoke-RestMethod -Uri "https://pypi.org/pypi/$Package/json" | Out-Null
        return $Package
    } catch {
        # Nothing published yet: install straight from the default branch.
        return "https://github.com/$Repo/archive/refs/heads/main.tar.gz"
    }
}

function Add-ToUserPath {
    param([string]$Directory)
    if ($NoModifyPath) { return $false }
    $current = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($null -eq $current) { $current = '' }
    if (($current -split ';') -contains $Directory) { return $false }
    $updated = if ($current.TrimEnd(';')) { "$($current.TrimEnd(';'));$Directory" } else { $Directory }
    [Environment]::SetEnvironmentVariable('Path', $updated, 'User')
    $env:Path = "$env:Path;$Directory"       # usable in this session too
    return $true
}

# --------------------------------------------------------------------------- #
# main                                                                         #
# --------------------------------------------------------------------------- #
if (-not $InstallDir) {
    $base = $env:LOCALAPPDATA
    if (-not $base) { $base = Join-Path $env:USERPROFILE 'AppData\Local' }
    $InstallDir = Join-Path $base 'nim-key-manager'
}
$RuntimeDir = Join-Path $InstallDir 'runtime'
$VenvDir = Join-Path $RuntimeDir 'venv'
$BinDir = if ($env:NIMKM_BIN_DIR) { $env:NIMKM_BIN_DIR } else { Join-Path $InstallDir 'bin' }
$VenvScripts = Join-Path $VenvDir 'Scripts'
$VenvNimkm = Join-Path $VenvScripts 'nimkm.exe'

Write-Host ''
Write-Host 'NIM Key Manager' -ForegroundColor White -NoNewline
Write-Host ' installer'
Write-Host $InstallDir -ForegroundColor DarkGray
Write-Host ''

New-Item -ItemType Directory -Force -Path $InstallDir, $RuntimeDir, $BinDir | Out-Null

$uv = Install-Uv -RuntimeDir $RuntimeDir

Write-Step 'creating an isolated runtime'
& $uv venv --python $PythonVersion --quiet $VenvDir
if ($LASTEXITCODE -ne 0) { Stop-WithError 'could not create the Python runtime' }

$resolved = Resolve-Source
if ($resolved -like '*.whl') { Write-Step 'installing the published release' }
elseif ($resolved -like '*.tar.gz') { Write-Step 'installing from the main branch (no release published yet)' }
else { Write-Step "installing $resolved" }

& $uv pip install --quiet --python (Join-Path $VenvScripts 'python.exe') $resolved
if ($LASTEXITCODE -ne 0) { Stop-WithError 'installation failed. See the output above.' }

& $VenvNimkm --version | Out-Null
if ($LASTEXITCODE -ne 0) { Stop-WithError "the installed command does not run; report it at https://github.com/$Repo/issues" }
$installed = (& $VenvNimkm --version) -replace '^nimkm\s+', ''
Write-Ok "installed nimkm $installed"

# Launchers: only `nimkm` goes on PATH, not the whole Scripts directory.
$cmdShim = @"
@echo off
if "%NIMKM_HOME%"=="" set "NIMKM_HOME=$InstallDir"
"$VenvNimkm" %*
"@
Set-Content -Path (Join-Path $BinDir 'nimkm.cmd') -Value $cmdShim -Encoding ASCII

$ps1Shim = @"
if (-not `$env:NIMKM_HOME) { `$env:NIMKM_HOME = '$InstallDir' }
& '$VenvNimkm' @args
exit `$LASTEXITCODE
"@
Set-Content -Path (Join-Path $BinDir 'nimkm.ps1') -Value $ps1Shim -Encoding UTF8
Write-Ok "launcher at $BinDir\nimkm.cmd"

$pathChanged = Add-ToUserPath -Directory $BinDir
if ($pathChanged) { Write-Ok "added $BinDir to your user PATH" }

$env:NIMKM_HOME = $InstallDir

if (-not $NoInit) {
    Write-Host ''
    & $VenvNimkm init --no-input
    if ($LASTEXITCODE -ne 0) { Stop-WithError "configuration failed; run '$BinDir\nimkm.cmd doctor'" }
}

# Editing the user's client configuration always asks first.
$mcpSetupDone = $false
if ((-not $NoMcpSetup) -and (-not $NoInit)) {
    Write-Host ''
    & $VenvNimkm mcp setup
    if ($LASTEXITCODE -eq 0) { $mcpSetupDone = $true }
}

Write-Host ''
Write-Host '----------------------------------------------------------' -ForegroundColor DarkGray
if ($pathChanged) {
    Write-Host 'Open a new terminal, then:'
} else {
    Write-Host 'Next:'
}
Write-Host ''
if (-not $mcpSetupDone) {
    Write-Host '    nimkm mcp setup' -ForegroundColor White -NoNewline
    Write-Host '   connect it to your MCP client'
}
Write-Host '    nimkm doctor' -ForegroundColor White -NoNewline
Write-Host '      check the installation'
Write-Host '    nimkm web' -ForegroundColor White -NoNewline
Write-Host '         administration dashboard (optional)'
Write-Host '    nimkm --help' -ForegroundColor White -NoNewline
Write-Host '      everything else'
Write-Host ''
