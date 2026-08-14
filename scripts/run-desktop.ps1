$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location $repoRoot

try {
$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if ($null -eq $uvCommand) {
  throw 'uv is required. Install it from https://docs.astral.sh/uv/getting-started/installation/.'
}

Write-Host 'Syncing Python dependencies with uv...'
& $uvCommand.Source sync --locked
if ($LASTEXITCODE -ne 0) {
  throw "uv sync --locked failed with exit code $LASTEXITCODE."
}

$pythonBin = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $pythonBin)) {
  throw "uv did not create the expected interpreter: $pythonBin"
}
$env:DSA_PYTHON = $pythonBin

Write-Host 'Building React UI (static assets)...'
Push-Location (Join-Path $repoRoot 'frontend\web')
if (!(Test-Path 'node_modules')) {
  npm install
}
npm run build
Pop-Location

Write-Host 'Starting Electron desktop (dev mode)...'
Push-Location (Join-Path $repoRoot 'frontend\desktop')
if (!(Test-Path 'node_modules')) {
  npm install
}
npm run dev
Pop-Location
} finally {
  Pop-Location
}
