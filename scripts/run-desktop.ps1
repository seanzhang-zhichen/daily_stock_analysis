$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

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
