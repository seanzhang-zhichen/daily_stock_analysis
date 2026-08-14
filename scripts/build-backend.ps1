$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location $repoRoot

try {
Write-Host 'Building React UI (static assets)...'
Push-Location (Join-Path $repoRoot 'frontend\web')
if (!(Test-Path 'node_modules')) {
  npm install
}
npm run build
Pop-Location

$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if ($null -eq $uvCommand) {
  throw 'uv is required. Install it from https://docs.astral.sh/uv/getting-started/installation/.'
}

$pythonRequest = $env:PYTHON_BIN
$syncArgs = @('sync', '--locked')
if (-not [string]::IsNullOrWhiteSpace($pythonRequest)) {
  $syncArgs += @('--python', $pythonRequest)
}

Write-Host 'Syncing Python dependencies with uv...'
& $uvCommand.Source @syncArgs
if ($LASTEXITCODE -ne 0) {
  throw "uv sync --locked failed with exit code $LASTEXITCODE."
}

$pythonBin = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $pythonBin)) {
  throw "uv did not create the expected interpreter: $pythonBin"
}
Write-Host "Using Python: $pythonBin"

Write-Host 'Verifying static asset references (source)...'
& $pythonBin "${PSScriptRoot}\check_static_assets.py" 'static'
if ($LASTEXITCODE -ne 0) {
  throw "Static asset sanity check failed for source static/. See GitHub #1064."
}

function Test-PythonCode {
  param(
    [string]$Python,
    [string]$Code
  )

  try {
    & $Python -c $Code *> $null
    return ($LASTEXITCODE -eq 0)
  } catch {
    return $false
  }
}

Write-Host 'Building backend executable...'
if (-not (Test-PythonCode -Python $pythonBin -Code "import PyInstaller")) {
  throw 'PyInstaller is not importable after uv sync.'
}

Write-Host 'Checking python-multipart availability...'
if (-not (Test-PythonCode -Python $pythonBin -Code "import multipart, multipart.multipart")) {
  throw 'python-multipart is not importable in the selected Python environment.'
}

if (Test-Path 'dist\backend') {
  Remove-Item -Recurse -Force 'dist\backend'
}
New-Item -ItemType Directory -Path 'dist\backend' | Out-Null

if (Test-Path 'dist\stock_analysis') {
  Remove-Item -Recurse -Force 'dist\stock_analysis'
}

if (Test-Path 'build\stock_analysis') {
  Remove-Item -Recurse -Force 'build\stock_analysis'
}

$hiddenImports = @(
  'multipart',
  'multipart.multipart',
  'json_repair',
  'tiktoken',
  'tiktoken_ext',
  'tiktoken_ext.openai_public',
  'api',
  'api.app',
  'api.deps',
  'api.v1',
  'api.v1.router',
  'api.v1.endpoints',
  'api.v1.endpoints.analysis',
  'api.v1.endpoints.history',
  'api.v1.endpoints.stocks',
  'api.v1.endpoints.health',
  'api.v1.schemas',
  'api.v1.schemas.analysis',
  'api.v1.schemas.history',
  'api.v1.schemas.stocks',
  'api.v1.schemas.common',
  'api.middlewares',
  'api.middlewares.error_handler',
  'src.services',
  'src.services.task_queue',
  'src.services.analysis_service',
  'src.services.history_service',
  'uvicorn.logging',
  'uvicorn.loops',
  'uvicorn.loops.auto',
  'uvicorn.protocols',
  'uvicorn.protocols.http',
  'uvicorn.protocols.http.auto',
  'uvicorn.protocols.websockets',
  'uvicorn.protocols.websockets.auto',
  'uvicorn.lifespan',
  'uvicorn.lifespan.on'
)
$hiddenImportArgs = $hiddenImports | ForEach-Object { "--hidden-import=$_" }

$pyInstallerArgs = @(
  '-m', 'PyInstaller',
  '--name', 'stock_analysis',
  '--onedir',
  '--noconfirm',
  '--noconsole',
  '--add-data', 'static;static',
  '--paths', 'backend',
  '--collect-data', 'litellm',
  '--collect-data', 'tiktoken'
)
$pyInstallerArgs += $hiddenImportArgs
$pyInstallerArgs += 'backend/main.py'

Write-Host "Running: $pythonBin $($pyInstallerArgs -join ' ')"
& $pythonBin @pyInstallerArgs
if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller failed with exit code $LASTEXITCODE."
}

if (!(Test-Path 'dist\stock_analysis')) {
  throw 'PyInstaller finished but dist\stock_analysis was not generated.'
}

Copy-Item -Path 'dist\stock_analysis' -Destination 'dist\backend\stock_analysis' -Recurse -Force

Write-Host 'Verifying static asset references (packaged)...'
$packagedStatic = Join-Path 'dist\backend\stock_analysis' '_internal\static'
if (-not (Test-Path $packagedStatic)) {
  $packagedStatic = Join-Path 'dist\backend\stock_analysis' 'static'
}
if (Test-Path $packagedStatic) {
  & $pythonBin "${PSScriptRoot}\check_static_assets.py" $packagedStatic
  if ($LASTEXITCODE -ne 0) {
    throw "Static asset sanity check failed for packaged $packagedStatic. See GitHub #1064."
  }
} else {
  Write-Warning "Could not locate packaged static directory under dist\backend\stock_analysis; skipping post-package check."
}

Write-Host 'Backend build completed.'
} finally {
  Pop-Location
}
