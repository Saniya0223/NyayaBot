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
