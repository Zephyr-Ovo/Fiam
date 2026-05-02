# scripts/deploy_favilla.ps1
# One-shot: push -> wait CI -> download APK artifact -> adb install
#
# Requires:
#   - C:\Users\Iris\.fiam\github_pat.dpapi  (DPAPI-encrypted GitHub PAT, scope: repo + workflow + actions:read)
#   - D:\scrcpy-win64-v3.3.4\adb.exe
#   - Repo origin is GitHub (owner/repo parsed from `git remote get-url origin`)
#
# Usage:
#   pwsh scripts/deploy_favilla.ps1            # push current branch and install
#   pwsh scripts/deploy_favilla.ps1 -SkipPush  # just grab latest artifact and install

param(
    [switch]$SkipPush,
    [string]$Branch = "",
    [string]$Workflow = "favilla-android.yml",
    [string]$Adb = "D:\scrcpy-win64-v3.3.4\adb.exe",
    [int]$PollSeconds = 15,
    [int]$TimeoutMinutes = 20
)

$ErrorActionPreference = "Stop"
Set-Location (git rev-parse --show-toplevel)

# --- 1. Decrypt PAT ---
$patPath = "$env:USERPROFILE\.fiam\github_pat.dpapi"
if (-not (Test-Path $patPath)) { throw "PAT not found at $patPath" }
Add-Type -AssemblyName System.Security
$raw = [IO.File]::ReadAllBytes($patPath)
# File may be raw DPAPI bytes OR a hex/base64 string. Detect.
try {
    $enc = $raw
    $dec = [System.Security.Cryptography.ProtectedData]::Unprotect($enc, $null, 'CurrentUser')
} catch {
    $txt = [Text.Encoding]::UTF8.GetString($raw).Trim()
    if ($txt -match '^[0-9a-fA-F]+$') {
        $enc = [byte[]]::new($txt.Length / 2)
        for ($i = 0; $i -lt $enc.Length; $i++) { $enc[$i] = [Convert]::ToByte($txt.Substring($i*2,2),16) }
    } else {
        $enc = [Convert]::FromBase64String($txt)
    }
    $dec = [System.Security.Cryptography.ProtectedData]::Unprotect($enc, $null, 'CurrentUser')
}
$pat = [Text.Encoding]::Unicode.GetString($dec).Trim()

# --- 2. Parse owner/repo ---
$origin = (git remote get-url origin).Trim()
if ($origin -match 'github\.com[:/](?<o>[^/]+)/(?<r>[^/.]+)') {
    $owner = $Matches.o; $repo = $Matches.r
} else { throw "Cannot parse GitHub origin: $origin" }
if (-not $Branch) { $Branch = (git rev-parse --abbrev-ref HEAD).Trim() }
Write-Host "[deploy] $owner/$repo @ $Branch" -ForegroundColor Cyan

$headers = @{ Authorization = "Bearer $pat"; Accept = "application/vnd.github+json"; "X-GitHub-Api-Version" = "2022-11-28"; "User-Agent" = "fiam-deploy-script" }
$api = "https://api.github.com/repos/$owner/$repo"

# --- 3. Push (capture target SHA) ---
if (-not $SkipPush) {
    Write-Host "[deploy] git push origin $Branch" -ForegroundColor Cyan
    git push origin $Branch
}
$targetSha = (git rev-parse HEAD).Trim()
Write-Host "[deploy] target SHA $targetSha" -ForegroundColor DarkGray

# --- 4. Poll workflow runs for this SHA ---
$deadline = (Get-Date).AddMinutes($TimeoutMinutes)
$run = $null
Write-Host "[deploy] waiting for workflow run on $Workflow ..." -ForegroundColor Cyan
while ((Get-Date) -lt $deadline) {
    $branchEnc = [System.Uri]::EscapeDataString($Branch)
    $resp = Invoke-RestMethod -Uri "$api/actions/workflows/$Workflow/runs?branch=$branchEnc&per_page=10" -Headers $headers
    $run = $resp.workflow_runs | Where-Object { $_.head_sha -eq $targetSha } | Select-Object -First 1
    if ($run) {
        $st = $run.status; $cn = $run.conclusion
        Write-Host ("  run #{0} status={1} conclusion={2}" -f $run.run_number, $st, $cn) -ForegroundColor DarkGray
        if ($st -eq "completed") {
            if ($cn -ne "success") { throw "Workflow concluded $cn — see $($run.html_url)" }
            break
        }
    } else {
        Write-Host "  (no run yet for SHA, waiting...)" -ForegroundColor DarkGray
    }
    Start-Sleep -Seconds $PollSeconds
}
if (-not $run -or $run.status -ne "completed") { throw "Timeout after $TimeoutMinutes min" }

# --- 5. Download artifact ---
$arts = Invoke-RestMethod -Uri "$api/actions/runs/$($run.id)/artifacts" -Headers $headers
$art = $arts.artifacts | Where-Object { $_.name -eq "favilla-debug-apk" } | Select-Object -First 1
if (-not $art) { throw "Artifact 'favilla-debug-apk' not found" }
$tmp = Join-Path $env:TEMP "favilla-apk-$($run.id)"
New-Item -Type Directory -Force -Path $tmp | Out-Null
$zip = Join-Path $tmp "apk.zip"
Write-Host "[deploy] downloading artifact ($([math]::Round($art.size_in_bytes/1MB,2)) MB) ..." -ForegroundColor Cyan
Invoke-WebRequest -Uri $art.archive_download_url -Headers $headers -OutFile $zip
Expand-Archive -Path $zip -DestinationPath $tmp -Force
$apk = Get-ChildItem -Path $tmp -Filter "*.apk" -Recurse | Select-Object -First 1
if (-not $apk) { throw "No APK inside artifact zip" }
Write-Host "[deploy] APK: $($apk.FullName)" -ForegroundColor Green

# --- 6. adb install ---
& $Adb devices | Out-Host
Write-Host "[deploy] adb install -r -d ..." -ForegroundColor Cyan
& $Adb install -r -d $apk.FullName
if ($LASTEXITCODE -ne 0) {
    Write-Host "[deploy] install failed, trying uninstall + reinstall ..." -ForegroundColor Yellow
    & $Adb uninstall cc.fiet.favilla | Out-Host
    & $Adb install $apk.FullName
    if ($LASTEXITCODE -ne 0) { throw "adb install failed (exit $LASTEXITCODE)" }
}
Write-Host "[deploy] done." -ForegroundColor Green
