#!/usr/bin/env pwsh
# fiam-tunnel.ps1 — Persistent reverse SSH tunnel: Local → Relay → ISP
# Allows AI on ISP to reach Local via ISP:2222
#
# Topology:
#   Local (Zephyr) → 38.47.118.220 (Relay, LA) → 99.173.22.93 (ISP)
#   Relay is pure jump host — NO operations there, traffic passes through only
#
# Install as startup task (run once, elevated PowerShell):
#   $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"F:\fiam-code\scripts\fiam-tunnel.ps1`""
#   $trigger = New-ScheduledTaskTrigger -AtLogon -User $env:USERNAME
#   $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
#   Register-ScheduledTask -TaskName "FiamTunnel" -Action $action -Trigger $trigger -Settings $settings -Description "Reverse SSH tunnel to ISP for fiam AI access"
#
# The tunnel maps:
#   Local :22 → ISP :2222 (reverse, via Relay jump)
#   ISP :8819 → DO :8819 (forward, embedding API — AI on ISP reaches DO)
#
# From ISP, AI runs: ssh -p 2222 Aurora@127.0.0.1

$RELAY = "root@38.47.118.220"
$ISP = "root@99.173.22.93"
$DO_EMBED = "209.38.69.231"
$RETRY_SECONDS = 30

while ($true) {
    Write-Host "[fiam-tunnel] $(Get-Date -Format 'HH:mm:ss') Establishing tunnel (Local → Relay → ISP)..."
    
    # -J $RELAY              → Jump through Relay (LA), no operations there
    # -R 2222:127.0.0.1:22   → AI on ISP can SSH into Local via ISP:2222
    # -L 8819:${DO_EMBED}:8819 → Local can reach DO's embedding API via localhost:8819
    # -N                     → No remote command
    # -o ServerAliveInterval → Keep alive every 30s
    # -o ServerAliveCountMax → Disconnect after 3 missed pings (reconnect via loop)
    # -o ExitOnForwardFailure → Don't silently fail if port already bound
    ssh -N `
        -J $RELAY `
        -R 2222:127.0.0.1:22 `
        -L 8819:${DO_EMBED}:8819 `
        -o ServerAliveInterval=30 `
        -o ServerAliveCountMax=3 `
        -o ExitOnForwardFailure=yes `
        -o StrictHostKeyChecking=no `
        $ISP

    Write-Host "[fiam-tunnel] $(Get-Date -Format 'HH:mm:ss') Tunnel dropped, retrying in ${RETRY_SECONDS}s..."
    Start-Sleep -Seconds $RETRY_SECONDS
}
