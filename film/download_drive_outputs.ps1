param(
    [Parameter(Mandatory)][string]$RemoteLog,
    [Parameter(Mandatory)][string]$OutDir,
    [string]$Manifest = (Join-Path (Split-Path -Parent $RemoteLog) 'drive_manifest.json')
)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$files = @()
foreach ($line in Get-Content -LiteralPath $RemoteLog) {
    if ($line -match '^DRIVE_FILE=(.+)$') { $files += ($Matches[1] | ConvertFrom-Json) }
}
if ($files.Count -eq 0) { throw "remote log has no DRIVE_FILE manifest entries: $RemoteLog" }
$verified = @()
foreach ($file in $files) {
    $destination = Join-Path $OutDir $file.name
    & gog drive download $file.id --out $destination --no-input | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "gog drive download failed for $($file.id): exit $LASTEXITCODE" }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $destination).Hash.ToLowerInvariant()
    if ($actual -ne $file.sha256) { throw "SHA-256 mismatch for $($file.name): remote=$($file.sha256) local=$actual" }
    $verified += [pscustomobject]@{ id=$file.id; name=$file.name; size=$file.size; remote_sha256=$file.sha256; local_sha256=$actual; matched=$true }
    Write-Output "SHA256_OK=$($file.name):$actual"
}
$verified | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $Manifest -Encoding utf8
Write-Output "MANIFEST=$Manifest"
