#!/usr/bin/env pwsh
# fiam-tunnel.ps1 — Persistent SSH tunnel: Local → Relay → ISP
# Forward-only: Local reaches ISP services, ISP reaches DO embedding API.
# NO reverse tunnel — ISP cannot SSH into Local (security decision).
#
# Topology:
#   Local (Zephyr) → 38.47.118.220 (Relay, LA) → 99.173.22.93 (ISP)
#   Relay is pure jump host — NO operations there, traffic passes through only
#
# Install as startup task (run once, elevated PowerShell):
#   $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"F:\fiam-code\scripts\fiam-tunnel.ps1`""
#   $trigger = New-ScheduledTaskTrigger -AtLogon -User $env:USERNAME
#   $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
#   Register-ScheduledTask -TaskName "FiamTunnel" -Action $action -Trigger $trigger -Settings $settings -Description "SSH tunnel to ISP for fiam"
#
# The tunnel maps:
#   ISP :8819 → DO :8819 (forward, embedding API — AI on ISP reaches DO)

$RELAY = "relay"          # ~/.ssh/config: root@38.47.118.220:2222
$ISP = "isp"              # ~/.ssh/config: root@99.173.22.93 (via relay jump)
$DO_EMBED = "209.38.69.231"
$RETRY_SECONDS = 30

while ($true) {
    Write-Host "[fiam-tunnel] $(Get-Date -Format 'HH:mm:ss') Establishing tunnel (Local → Relay → ISP)..."
    
    # -J $RELAY              → Jump through Relay (LA), no operations there
    # -L 8819:${DO_EMBED}:8819 → Local can reach DO's embedding API via localhost:8819
    # -N                     → No remote command
    # -o ServerAliveInterval → Keep alive every 30s
    # -o ServerAliveCountMax → Disconnect after 3 missed pings (reconnect via loop)
    # -o ExitOnForwardFailure → Don't silently fail if port already bound
    ssh -N `
        -J $RELAY `
        -L 8819:${DO_EMBED}:8819 `
        -o ServerAliveInterval=30 `
        -o ServerAliveCountMax=3 `
        -o ExitOnForwardFailure=yes `
        -o StrictHostKeyChecking=no `
        $ISP

    Write-Host "[fiam-tunnel] $(Get-Date -Format 'HH:mm:ss') Tunnel dropped, retrying in ${RETRY_SECONDS}s..."
    Start-Sleep -Seconds $RETRY_SECONDS
}
