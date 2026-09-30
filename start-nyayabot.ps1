<#
    Starts the NyayaBot backend and frontend in two visible terminals.

    Dependencies are never installed here: backend\.venv and node_modules are
    expected to exist already, so startup stays fast and predictable.

    Use -Force to stop anything already holding port 8000 or 3000 before
    starting. Without it, an occupied port is reported and nothing is started.
#>
[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDirectory = Join-Path $projectRoot "backend"
$frontendDirectory = Join-Path $projectRoot "frontend"
$backendPython = Join-Path $backendDirectory ".venv\Scripts\python.exe"
$frontendPackage = Join-Path $frontendDirectory "package.json"

if (-not (Test-Path -LiteralPath $backendPython -PathType Leaf)) {
    Write-Error "The backend Python environment was not found at '$backendPython'. Create backend\.venv and install the backend dependencies once before using this launcher."
    exit 1
}

if (-not (Test-Path -LiteralPath $frontendPackage -PathType Leaf)) {
    Write-Error "The frontend package file was not found at '$frontendPackage'."
    exit 1
}

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Error "npm was not found in PATH. Install Node.js once before using this launcher."
    exit 1
}

# A leftover uvicorn or next process keeps the port, so the new terminal dies
# immediately with an "address already in use" error scrolled off screen. The
# project notes list duplicate uvicorn processes as a recurring failure, so
# check first and say plainly what is holding the port.
function Get-PortOwner {
    param([Parameter(Mandatory = $true)][int]$Port)

    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $listener) { return $null }

    $process = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
    return [PSCustomObject]@{
        Port = $Port
        Pid  = $listener.OwningProcess
        Name = if ($process) { $process.ProcessName } else { "unknown" }
    }
}

$occupied = @(8000, 3000 | ForEach-Object { Get-PortOwner -Port $_ } | Where-Object { $_ })

if ($occupied.Count -gt 0) {
    if ($Force) {
        foreach ($entry in $occupied) {
            Write-Host "Stopping $($entry.Name) (PID $($entry.Pid)) on port $($entry.Port)..."
            Stop-Process -Id $entry.Pid -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Milliseconds 800
    }
    else {
        foreach ($entry in $occupied) {
            Write-Warning "Port $($entry.Port) is already in use by $($entry.Name) (PID $($entry.Pid))."
        }
        Write-Host ""
        Write-Host "NyayaBot may already be running - check http://localhost:3000 first."
        Write-Host "To restart anyway, run: .\start-nyayabot.bat -Force"
        exit 1
    }
}

function ConvertTo-EncodedPowerShellCommand {
    param([Parameter(Mandatory = $true)][string]$Command)

    return [Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes($Command))
}

$backendCommand = @'
$Host.UI.RawUI.WindowTitle = "NyayaBot Backend - localhost:8000"
& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --reload
'@

$frontendCommand = @'
$Host.UI.RawUI.WindowTitle = "NyayaBot Frontend - localhost:3000"
npm run dev
'@

Start-Process -FilePath "powershell.exe" `
    -WorkingDirectory $backendDirectory `
    -ArgumentList "-NoExit", "-EncodedCommand", (ConvertTo-EncodedPowerShellCommand $backendCommand)

Start-Process -FilePath "powershell.exe" `
    -WorkingDirectory $frontendDirectory `
    -ArgumentList "-NoExit", "-EncodedCommand", (ConvertTo-EncodedPowerShellCommand $frontendCommand)

Write-Host "NyayaBot development terminals started."
Write-Host "Frontend: http://localhost:3000"
Write-Host "Backend:  http://localhost:8000"
