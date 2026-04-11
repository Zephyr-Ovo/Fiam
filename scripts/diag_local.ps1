#!/usr/bin/env pwsh
# diag_local.ps1 — 本地网络干扰排查（iris 用户下运行）
# powershell -ExecutionPolicy Bypass -File F:\fiam-code\scripts\diag_local.ps1

$sep = "`n" + ("=" * 60)

Write-Host "$sep`n[1] 当前用户 / 网卡 / TUN 残留" -ForegroundColor Cyan
whoami
Get-NetAdapter | Select-Object Name, Status, InterfaceDescription, ifIndex | Format-Table -AutoSize

Write-Host "$sep`n[2] 完整路由表（查残留路由）" -ForegroundColor Cyan
Get-NetRoute -AddressFamily IPv4 | Where-Object { 
    $_.DestinationPrefix -match "^0\.0\.0\.0|^209\.38\.69|^99\.173\.22|^172\.18\." -or
    $_.InterfaceAlias -match "tun|singbox|wintun"
} | Select-Object DestinationPrefix, NextHop, RouteMetric, InterfaceAlias | Format-Table -AutoSize

Write-Host "$sep`n[3] 所有默认路由" -ForegroundColor Cyan
Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue | Format-Table InterfaceAlias, NextHop, RouteMetric, InterfaceMetric -AutoSize

Write-Host "$sep`n[4] Windows 防火墙 — 出站阻止规则" -ForegroundColor Cyan
$blockRules = Get-NetFirewallRule -Direction Outbound -Action Block -Enabled True -ErrorAction SilentlyContinue
if ($blockRules) {
    $blockRules | Select-Object DisplayName, Profile, Owner | Format-Table -AutoSize
    Write-Host "共 $($blockRules.Count) 条出站阻止规则"
} else {
    Write-Host "无出站阻止规则"
}

Write-Host "$sep`n[5] 防火墙 — 涉及 Code/xray/v2ray/ssh 的规则" -ForegroundColor Cyan
Get-NetFirewallRule -Enabled True -ErrorAction SilentlyContinue | Where-Object {
    $_.DisplayName -match "code|xray|v2ray|ssh|sing|fiam|tunnel|visual studio"
} | Select-Object DisplayName, Direction, Action, Profile | Format-Table -AutoSize

Write-Host "$sep`n[6] 计划任务 — 网络/隧道相关" -ForegroundColor Cyan
Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {
    $_.TaskName -match "fiam|tunnel|ssh|proxy|v2ray|xray|sing|tun"
} | Select-Object TaskName, State, TaskPath | Format-Table -AutoSize
Write-Host "(空=无相关计划任务)"

Write-Host "$sep`n[7] WinHTTP 代理（系统级，非 IE 注册表）" -ForegroundColor Cyan
netsh winhttp show proxy

Write-Host "$sep`n[8] 后台网络进程全量" -ForegroundColor Cyan
Get-Process | Where-Object {
    $_.ProcessName -match 'ssh|xray|v2ray|sing|clash|mihomo|tun2socks|wintun|privoxy|proxifier|cloudflare|warp|wireguard|openvpn|frp|ngrok|tunnel'
} | Select-Object ProcessName, Id, SessionId, StartTime | Format-Table -AutoSize
Write-Host "(空=无相关进程)"

Write-Host "$sep`n[9] TCP 连接到 DO:443 和 DO:4433 的当前状态" -ForegroundColor Cyan
Get-NetTCPConnection -RemoteAddress "209.38.69.231" -ErrorAction SilentlyContinue | 
    Select-Object LocalPort, RemotePort, State, OwningProcess,
    @{N='Process';E={(Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName}} |
    Format-Table -AutoSize
Write-Host "(空=当前无到 DO 的 TCP 连接)"

Write-Host "$sep`n[10] Winsock / TCP 全局设置（可能被代理工具篡改）" -ForegroundColor Cyan
netsh winsock show catalog | Select-String "Description|Provider" | Select-Object -First 20
Write-Host "---"
Write-Host "TCP autotuning:"
netsh interface tcp show global | Select-String "Auto Tuning|ECN|Timestamps|Chimney|RSS|DCA"

Write-Host "$sep`n[11] MTU 值" -ForegroundColor Cyan
netsh interface ipv4 show subinterfaces | Select-String "WLAN|Loopback|tun|singbox|vEthernet"

Write-Host "$sep`n[12] hosts 文件（查劫持）" -ForegroundColor Cyan
$hosts = Get-Content "$env:SystemRoot\System32\drivers\etc\hosts" -ErrorAction SilentlyContinue | 
    Where-Object { $_ -notmatch '^\s*#' -and $_.Trim() -ne '' }
if ($hosts) {
    Write-Host "有自定义 hosts 条目:" -ForegroundColor Yellow
    $hosts
} else {
    Write-Host "hosts 文件干净（无自定义条目）"
}

Write-Host "$sep`n[13] Loopback 连通性（VSCode 内部通信）" -ForegroundColor Cyan
$tcp = New-Object System.Net.Sockets.TcpClient
try {
    $tcp.Connect("127.0.0.1", 10808)
    Write-Host "127.0.0.1:10808 可达" -ForegroundColor Green
    $tcp.Close()
} catch {
    Write-Host "127.0.0.1:10808 不可达!" -ForegroundColor Red
}

Write-Host "$sep`n[14] 实际 curl 测试（走代理 vs 不走）" -ForegroundColor Cyan
Write-Host "--- 走代理 → marketplace (模拟 VSCode) ---"
try {
    $proxy = New-Object System.Net.WebProxy("http://127.0.0.1:10808")
    $wc = New-Object System.Net.WebClient
    $wc.Proxy = $proxy
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $result = $wc.DownloadString("https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery")
    $sw.Stop()
    Write-Host "OK in $($sw.ElapsedMilliseconds)ms, size=$($result.Length)" -ForegroundColor Green
    $wc.Dispose()
} catch {
    Write-Host "FAILED: $($_.Exception.InnerException.Message ?? $_.Exception.Message)" -ForegroundColor Red
}

Write-Host "--- 走代理 → github.com ---"
try {
    $proxy = New-Object System.Net.WebProxy("http://127.0.0.1:10808")
    $wc = New-Object System.Net.WebClient
    $wc.Proxy = $proxy
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $result = $wc.DownloadString("https://github.com")
    $sw.Stop()
    Write-Host "OK in $($sw.ElapsedMilliseconds)ms, size=$($result.Length)" -ForegroundColor Green
    $wc.Dispose()
} catch {
    Write-Host "FAILED: $($_.Exception.InnerException.Message ?? $_.Exception.Message)" -ForegroundColor Red
}

Write-Host "$sep`n[15] IP 代理链路验证" -ForegroundColor Cyan
try {
    $proxy = New-Object System.Net.WebProxy("http://127.0.0.1:10808")
    $wc = New-Object System.Net.WebClient
    $wc.Proxy = $proxy
    $ip = $wc.DownloadString("https://api.ipify.org")
    Write-Host "代理出口 IP: $ip"
    if ($ip -eq "99.173.22.93") {
        Write-Host "ISP 出口确认 ✓" -ForegroundColor Green
    } elseif ($ip -eq "209.38.69.231") {
        Write-Host "DO 直出（4433 节点？）" -ForegroundColor Yellow
    } else {
        Write-Host "未知 IP — 检查代理配置!" -ForegroundColor Red
    }
    $wc.Dispose()
} catch {
    Write-Host "IP 检测失败: $($_.Exception.InnerException.Message ?? $_.Exception.Message)" -ForegroundColor Red
}

Write-Host "$sep`n[DONE] 贴回全部输出" -ForegroundColor Green
