param([string]$Root = (Split-Path -Parent $PSScriptRoot))
$out = Join-Path $Root 'candidate_output\installer_staging'
if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Recurse -Force }
New-Item -ItemType Directory -Path $out -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $Root 'dist\CompactMe') -Destination (Join-Path $out 'full\CompactMe') -Recurse
Copy-Item -LiteralPath (Join-Path $Root 'dist\CompactMe') -Destination (Join-Path $out 'apponly\CompactMe') -Recurse
New-Item -ItemType File -Path (Join-Path $out 'full\CompactMe\_internal\full.marker') -Force | Out-Null
New-Item -ItemType File -Path (Join-Path $out 'apponly\CompactMe\_internal\apponly.marker') -Force | Out-Null
Remove-Item -LiteralPath (Join-Path $out 'apponly\CompactMe\_internal\bin') -Recurse -Force
Remove-Item -LiteralPath (Join-Path $out 'apponly\CompactMe\_internal\manifest.json') -Force -ErrorAction SilentlyContinue
Copy-Item -LiteralPath (Join-Path $Root 'candidate_output\shared-media-runtime\runtimes\media-0.1.0-win-x64-candidate') -Destination (Join-Path $out 'runtime\media\runtimes\media-0.1.0-win-x64-candidate') -Recurse
Copy-Item -LiteralPath (Join-Path $Root 'candidate_output\shared-media-runtime\catalog.json') -Destination (Join-Path $out 'runtime\media\catalog.json')
Write-Output $out
