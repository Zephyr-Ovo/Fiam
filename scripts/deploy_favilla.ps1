# scripts/deploy_favilla.ps1
# Pull latest → wait for CI → download APK → uninstall + install
#
# Requires:
#   - ~/.fiam/github_pat.dpapi  (DPAPI-encrypted GitHub PAT)
#   - D:\scrcpy-win64-v3.3.4\adb.exe (phone connected)
#   - Repo cloned at current directory (or specify -RepoRoot)
#
# Usage:
#   cd F:\Fiam
#   pwsh scripts/deploy_favilla.ps1

param(
    [string]$Workflow = "favilla-android.yml",
    [string]$Adb = "D:\scrcpy-win64-v3.3.4\adb.exe",
    [int]$PollSeconds = 15,
    [int]$TimeoutMinutes = 15
)

$ErrorActionPreference = "Stop"
Set-Location (git rev-parse --show-toplevel)

# --- 1. Pull latest ---
Write-Host "[deploy] git pull ..." -ForegroundColor Cyan
git pull --ff-only origin main
$targetSha = (git rev-parse HEAD).Trim()
Write-Host "[deploy] HEAD: $targetSha" -ForegroundColor DarkGray

# --- 2. Decrypt PAT ---
$patPath = "$env:USERPROFILE\.fiam\github_pat.dpapi"
if (-not (Test-Path $patPath)) { throw "PAT not found at $patPath" }
Add-Type -AssemblyName System.Security
$raw = [IO.File]::ReadAllBytes($patPath)
try {
    $dec = [System.Security.Cryptography.ProtectedData]::Unprotect($raw, $null, 'CurrentUser')
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

# --- 3. Parse owner/repo ---
$origin = (git remote get-url origin).Trim()
if ($origin -match 'github\.com[:/](?<o>[^/]+)/(?<r>[^/.]+)') {
    $owner = $Matches.o; $repo = $Matches.r
} else { throw "Cannot parse GitHub origin: $origin" }

$headers = @{ Authorization = "Bearer $pat"; Accept = "application/vnd.github+json"; "X-GitHub-Api-Version" = "2022-11-28"; "User-Agent" = "fiam-deploy" }
$api = "https://api.github.com/repos/$owner/$repo"

# --- 4. Wait for CI to complete on this SHA ---
$deadline = (Get-Date).AddMinutes($TimeoutMinutes)
$run = $null
Write-Host "[deploy] waiting for CI on $($targetSha.Substring(0,8)) ..." -ForegroundColor Cyan
while ((Get-Date) -lt $deadline) {
    $resp = Invoke-RestMethod -Uri "$api/actions/workflows/$Workflow/runs?per_page=5" -Headers $headers
    $run = $resp.workflow_runs | Where-Object { $_.head_sha -eq $targetSha } | Select-Object -First 1
    if ($run) {
        $st = $run.status; $cn = $run.conclusion
        Write-Host ("  run #{0} {1} {2}" -f $run.run_number, $st, $cn) -ForegroundColor DarkGray
        if ($st -eq "completed") {
            if ($cn -ne "success") { throw "CI failed ($cn) — $($run.html_url)" }
            break
        }
    } else {
        Write-Host "  (waiting for run...)" -ForegroundColor DarkGray
    }
    Start-Sleep -Seconds $PollSeconds
}
if (-not $run -or $run.status -ne "completed") { throw "Timeout after $TimeoutMinutes min" }

# --- 5. Download APK artifact ---
$arts = Invoke-RestMethod -Uri "$api/actions/runs/$($run.id)/artifacts" -Headers $headers
$art = $arts.artifacts | Where-Object { $_.name -eq "favilla-debug-apk" } | Select-Object -First 1
if (-not $art) { throw "Artifact 'favilla-debug-apk' not found" }
$tmp = Join-Path $env:TEMP "favilla-apk-$($run.id)"
New-Item -Type Directory -Force -Path $tmp | Out-Null
$zip = Join-Path $tmp "apk.zip"
Write-Host "[deploy] downloading ($([math]::Round($art.size_in_bytes/1MB,1)) MB) ..." -ForegroundColor Cyan
Invoke-WebRequest -Uri $art.archive_download_url -Headers $headers -OutFile $zip
Expand-Archive -Path $zip -DestinationPath $tmp -Force
$apk = Get-ChildItem -Path $tmp -Filter "*.apk" -Recurse | Select-Object -First 1
if (-not $apk) { throw "No APK in artifact" }
Write-Host "[deploy] APK: $($apk.FullName)" -ForegroundColor Green

# --- 6. Uninstall + install ---
& $Adb devices | Out-Host
Write-Host "[deploy] uninstall + install ..." -ForegroundColor Cyan
& $Adb uninstall cc.fiet.favilla 2>&1 | Out-Null
& $Adb install $apk.FullName
if ($LASTEXITCODE -ne 0) { throw "adb install failed (exit $LASTEXITCODE)" }
Write-Host "[deploy] done!" -ForegroundColor Green
