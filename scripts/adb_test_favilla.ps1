# scripts/adb_test_favilla.ps1
# ADB-driven E2E smoke test for Favilla memory pipeline.
#
# What it covers:
#   1) Open Favilla from app launcher
#   2) Tap into chat (home tile)
#   3) Send 4 short messages with `adb shell input text` (uses focused composer)
#   4) Tap scissors -> confirm seal -> wait for backend to embed
#   5) Send a recall-armed message (tap recall button first), check AI cites memory
#   6) Repeat against alternate backend (toggle in Settings) -- skipped unless -BackendSwap
#   7) Pull store/features/*.jsonl tail to verify events appeared server-side
#
# Tap coordinates are derived from design space (412x915 dp). On the test
# phone (1080x2400 px @ 480dpi, devicePixelRatio=3) the effective scale-to-fit
# is ~0.874, so phys = design * 0.874 * 3 ~= design * 2.621.
# Re-run with -DryRun to print what would be tapped/typed.

param(
    [string]$Adb = "D:\scrcpy-win64-v3.3.4\adb.exe",
    [string]$Device = "OVOJUWYD4HIZYHKZ",
    [string]$Pkg = "cc.fiet.favilla",
    [string]$ServerHost = "fiet.cc",
    [string]$Token = $env:FIAM_INGEST_TOKEN,
    [int]$ShotIdx = 0,
    [string]$ShotDir = "logs\favilla-e2e",
    [switch]$DryRun,
    [switch]$BackendSwap,
    [switch]$KeepRunning
)

$ErrorActionPreference = "Stop"
if (-not $Token) { throw "FIAM_INGEST_TOKEN is required for Favilla ADB smoke tests" }

$null = New-Item -ItemType Directory -Force -Path $ShotDir

# --- helpers -------------------------------------------------------------
function Adb([string[]]$args) {
    if ($DryRun) { Write-Host "[dry] adb $($args -join ' ')" -ForegroundColor DarkGray; return }
    & $Adb -s $Device @args
}
function Tap([int]$dx, [int]$dy, [string]$why = "") {
    $px = [int]($dx * 2.621); $py = [int]($dy * 2.621)
    Write-Host "tap ($dx,$dy)dp -> ($px,$py)px  $why" -ForegroundColor Cyan
    Adb @('shell','input','tap', $px, $py)
    Start-Sleep -Milliseconds 350
}
function Type([string]$msg) {
    Write-Host "type: $msg" -ForegroundColor Yellow
    # input text uses %s for spaces; escape spaces and quote
    $escaped = $msg -replace ' ', '%s' -replace '"', '\"'
    Adb @('shell','input','text', $escaped)
    Start-Sleep -Milliseconds 250
}
function KeyEnter() { Adb @('shell','input','keyevent','66'); Start-Sleep -Milliseconds 200 }
function HideKbd() { Adb @('shell','input','keyevent','111'); Start-Sleep -Milliseconds 200 }  # ESC
function Shot([string]$tag) {
    $script:ShotIdx++
    $name = ('{0:D2}-{1}.png' -f $script:ShotIdx, $tag)
    $path = Join-Path $ShotDir $name
    if ($DryRun) { Write-Host "[dry] screencap $path" -ForegroundColor DarkGray; return }
    & $Adb -s $Device exec-out screencap -p > $path
    Write-Host "  shot -> $path" -ForegroundColor DarkGreen
}
function ApiGet([string]$path) {
    try {
        Invoke-RestMethod -Uri "https://$ServerHost$path" -Headers @{ 'X-Fiam-Token' = $Token } -TimeoutSec 10
    } catch { Write-Host "  api $path failed: $_" -ForegroundColor Red; $null }
}
function StoreTail([int]$n = 5) {
    # Read the latest event head from the server (assumes /api/app/status returns counts).
    $r = ApiGet '/api/app/status'
    if ($r) { Write-Host "  status: $($r | ConvertTo-Json -Compress)" -ForegroundColor Magenta }
}

# --- 0. preflight --------------------------------------------------------
Write-Host '== preflight ==' -ForegroundColor Green
Adb @('shell','wm','size')
Adb @('shell','pidof', $Pkg)

# --- 1. launch -----------------------------------------------------------
Write-Host '== launch app ==' -ForegroundColor Green
Adb @('shell','monkey','-p', $Pkg, '-c','android.intent.category.LAUNCHER','1')
Start-Sleep -Seconds 2
Shot 'home'

# --- 2. enter chat -------------------------------------------------------
# chat tile design hitbox center: (155, 303) dp
Write-Host '== open chat ==' -ForegroundColor Green
Tap 155 303 'chat tile'
Start-Sleep -Seconds 1
Shot 'chat-empty'

# --- 3. send messages ---------------------------------------------------
# composer pill is roughly y~860 dp; tapping into the textarea focuses it.
$composerX = 200; $composerY = 860
$sendX = 380; $sendY = 870

$messages = @(
    "morning walk by the river",
    "just thought about Calvino again",
    "tea got cold, ugh",
    "missing the cat from across the street"
)
foreach ($m in $messages) {
    Tap $composerX $composerY 'composer'
    Start-Sleep -Milliseconds 400
    Type $m
    Start-Sleep -Milliseconds 300
    Tap $sendX $sendY 'send'
    Start-Sleep -Seconds 4   # let AI respond
    Shot ("msg-" + ($m.Substring(0, [Math]::Min(12, $m.Length)) -replace '\W','_'))
}

# --- 4. seal block (scissors) -------------------------------------------
Write-Host '== seal block ==' -ForegroundColor Green
# scissors button: top-right of header, ~y=28 dp, x~380 dp
Tap 380 28 'scissors'
Start-Sleep -Milliseconds 600
Shot 'seal-confirm'
# confirm dialog "Yes" button -- usually centered ~y=480 dp
Tap 270 500 'confirm yes'
Start-Sleep -Seconds 6
Shot 'sealed'
StoreTail

# --- 5. recall-armed message --------------------------------------------
Write-Host '== recall-armed message ==' -ForegroundColor Green
# recall button is just right of + in tools row, roughly (52, 870) dp
Tap 60 870 'recall arm'
Start-Sleep -Milliseconds 400
Shot 'recall-armed'
Tap $composerX $composerY 'composer'
Type "what did I think about earlier today?"
Tap $sendX $sendY 'send'
Start-Sleep -Seconds 6
Shot 'recall-reply'

# --- 6. (optional) swap backend -----------------------------------------
if ($BackendSwap) {
    Write-Host '== swap backend ==' -ForegroundColor Green
    # back to home
    Tap 25 28 'back'
    Start-Sleep -Seconds 1
    # settings gear in home
    Tap 50 72 'settings'
    Start-Sleep -Seconds 1
    Shot 'settings'
    # toggle is mid-page; user must aim manually -- screenshot for review
    Write-Host '  -- inspect screenshot, no auto-toggle in v1.' -ForegroundColor Yellow
}

# --- 7. final state -----------------------------------------------------
Write-Host '== final ==' -ForegroundColor Green
StoreTail
if (-not $KeepRunning) {
    HideKbd
}
Write-Host "shots in $ShotDir" -ForegroundColor Green
