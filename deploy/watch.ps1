$ErrorActionPreference = 'Stop'
$appDir = $PSScriptRoot
$pythonw = Join-Path $appDir '.venv\Scripts\pythonw.exe'
$script = Join-Path $appDir 'watch.py'

if (-not (Test-Path -LiteralPath $pythonw)) { throw "pythonw not found: $pythonw" }
if (-not (Test-Path -LiteralPath $script)) { throw "watch.py not found: $script" }

Set-Location $appDir
# watch.py has a single-instance file lock (watch.lock); a duplicate launch exits by itself.
Start-Process -FilePath $pythonw -ArgumentList @($script) -WorkingDirectory $appDir -WindowStyle Hidden
