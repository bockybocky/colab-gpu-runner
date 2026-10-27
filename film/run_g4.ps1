param(
    [Parameter(Mandatory)][string]$Root,          # build_remote_job.py 的 --out-dir
    [Parameter(Mandatory)][string]$GoogleAccount, # gog 登入的 Google 帳號（成品存到這個帳號的雲端硬碟）
    [string]$RemoteJob = '',
    [string]$Session = 'g4-film-drive',
    # H3 兩段式峰值約 52GB 顯存：只收 G4（96GB）、A100（要配到 80GB 版）、H100（80GB，未實測）
    [ValidateSet('G4','A100','H100')][string]$Gpu = 'G4',
    [string]$DriveFolder = 'colab_outputs',
    [string]$GogCredentials = (Join-Path $env:APPDATA 'gogcli\credentials.json'),
    [string]$Colab = '',
    [switch]$SmokeTest,
    [switch]$EmitBase64
)
$ErrorActionPreference = 'Stop'
if (-not $Colab) {
    # uv tool install 預設裝在 ~/.local/bin，常常不在系統路徑上
    $Colab = if (Get-Command colab -ErrorAction SilentlyContinue) { 'colab' } else { Join-Path $HOME '.local/bin/colab.exe' }
}
if (-not $RemoteJob) { $RemoteJob = Join-Path $Root 'remote_job.py' }
$Out = Join-Path $Root 'out'
$RunLog = Join-Path $Root 'run.log'
$RemoteLog = Join-Path $Root 'remote.log'
$Manifest = Join-Path $Root 'drive_manifest.json'
$TokenExport = Join-Path ([System.IO.Path]::GetTempPath()) ("gog-token-{0}.json" -f [guid]::NewGuid())
$RunId = "g4-{0}" -f (Get-Date -Format 'yyyyMMdd-HHmmss')
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$started = Get-Date

function Log-Colab([string[]]$CommandArgs, [string]$Label) {
    "[$(Get-Date -Format o)] $Label" | Tee-Object -FilePath $RunLog -Append
    & $Colab @CommandArgs 2>&1 | Tee-Object -FilePath $RunLog -Append
    if ($LASTEXITCODE -ne 0) { throw "colab $($CommandArgs -join ' ') failed: exit $LASTEXITCODE" }
}

try {
    Log-Colab @('sessions') 'PRE_SESSIONS'
    Log-Colab @('usage') 'PRE_USAGE'
    # 雲端硬碟憑證只用環境變數傳進遠端機器，不寫進腳本、不寫進紀錄檔
    & gog auth tokens export $GoogleAccount --out $TokenExport --overwrite --no-input | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "gog token export failed: exit $LASTEXITCODE" }
    $refresh = (Get-Content -Raw -LiteralPath $TokenExport | ConvertFrom-Json).refresh_token
    $creds = Get-Content -Raw -LiteralPath $GogCredentials | ConvertFrom-Json
    $oauth = if ($creds.installed) { $creds.installed } elseif ($creds.web) { $creds.web } else { $creds }
    $base64Flag = if ($EmitBase64) { '1' } else { '0' }
    $smokeFlag = if ($SmokeTest) { '1' } else { '0' }
    # colab run：開新機器、跑完腳本自動釋放；長任務不要用 new＋exec（容易斷線）
    & $Colab run --gpu $Gpu --session $Session --timeout 4400 `
        --env "GDRIVE_REFRESH_TOKEN=$refresh" `
        --env "GDRIVE_CLIENT_ID=$($oauth.client_id)" `
        --env "GDRIVE_CLIENT_SECRET=$($oauth.client_secret)" `
        --env "GDRIVE_FOLDER=$DriveFolder" --env "GDRIVE_RUN_ID=$RunId" `
        --env "G4_EMIT_BASE64=$base64Flag" --env "G4_SMOKE_TEST=$smokeFlag" `
        $RemoteJob 2>&1 | Tee-Object -FilePath $RemoteLog
    if ($LASTEXITCODE -ne 0) { throw "remote job failed: exit $LASTEXITCODE" }
    $downloadStarted = Get-Date
    & (Join-Path $PSScriptRoot 'download_drive_outputs.ps1') -RemoteLog $RemoteLog -OutDir $Out -Manifest $Manifest
    if ($LASTEXITCODE -ne 0) { throw "Drive download failed: exit $LASTEXITCODE" }
    "DOWNLOAD_SECONDS=$([math]::Round(((Get-Date) - $downloadStarted).TotalSeconds,3))" | Tee-Object -FilePath $RunLog -Append
}
catch {
    "[$(Get-Date -Format o)] ERROR $($_.Exception.Message)" | Tee-Object -FilePath $RunLog -Append
    throw
}
finally {
    if (Test-Path -LiteralPath $TokenExport) { Remove-Item -LiteralPath $TokenExport -Force }
    $elapsed = ((Get-Date) - $started).TotalSeconds
    "TOTAL_WALL_SECONDS=$([math]::Round($elapsed,3))" | Tee-Object -FilePath $RunLog -Append
    # 收尾一定再看一次有沒有殘留機器：殘留的機器會照樣計費
    Log-Colab @('sessions') 'POST_SESSIONS'
    Log-Colab @('usage') 'POST_USAGE'
}
