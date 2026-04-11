#!/usr/bin/env pwsh
# diag_iris.ps1 — iris 用户网络/安全全面诊断
# 在 iris 账户的 PowerShell 中运行：powershell -ExecutionPolicy Bypass -File F:\fiam-code\scripts\diag_iris.ps1
# 输出贴回来即可

$sep = "`n" + ("=" * 60)

Write-Host "$sep`n[1/12] 基本信息" -ForegroundColor Cyan
whoami
Write-Host "Hostname: $env:COMPUTERNAME"
Write-Host "Time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')"

Write-Host "$sep`n[2/12] 网络适配器（查 TUN 虚拟网卡）" -ForegroundColor Cyan
Get-NetAdapter | Select-Object Name, Status, InterfaceDescription, MacAddress | Format-Table -AutoSize

Write-Host "$sep`n[3/12] 默认路由（TUN 是否抢占）" -ForegroundColor Cyan
Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue | 
    Select-Object InterfaceAlias, NextHop, RouteMetric, InterfaceMetric | Format-Table -AutoSize

Write-Host "$sep`n[4/12] DNS 配置" -ForegroundColor Cyan
Get-DnsClientServerAddress -AddressFamily IPv4 | 
    Where-Object { $_.ServerAddresses.Count -gt 0 } |
    Select-Object InterfaceAlias, ServerAddresses | Format-Table -AutoSize

Write-Host "$sep`n[5/12] 代理相关进程" -ForegroundColor Cyan
Get-Process | Where-Object { $_.ProcessName -match 'v2ray|xray|sing-box|tun2socks|clash|mihomo|hysteria|naive|trojan|ss-local|privoxy|proxifier' } |
    Select-Object ProcessName, Id, SessionId, StartTime, 
    @{N='CmdLine';E={ (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)" -ErrorAction SilentlyContinue).CommandLine }} |
    Format-Table -AutoSize -Wrap

Write-Host "$sep`n[6/12] 代理监听端口" -ForegroundColor Cyan
netstat -ano | Select-String "LISTENING" | Select-String "10808|10809|1080|1081|2080|2081|7890|7891|9090|10800|10801"

Write-Host "$sep`n[7/12] 系统代理设置（注册表）" -ForegroundColor Cyan
$inet = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings"
$proxyEn = (Get-ItemProperty $inet -ErrorAction SilentlyContinue).ProxyEnable
$proxyServer = (Get-ItemProperty $inet -ErrorAction SilentlyContinue).ProxyServer
$proxyOverride = (Get-ItemProperty $inet -ErrorAction SilentlyContinue).ProxyOverride
$autoConfigUrl = (Get-ItemProperty $inet -ErrorAction SilentlyContinue).AutoConfigURL
Write-Host "ProxyEnable:   $proxyEn"
Write-Host "ProxyServer:   $proxyServer"
Write-Host "ProxyOverride: $proxyOverride"
Write-Host "AutoConfigURL: $autoConfigUrl"

Write-Host "$sep`n[8/12] 环境变量（代理/安全相关）" -ForegroundColor Cyan
$dangerVars = @('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY',
                'http_proxy','https_proxy','all_proxy','no_proxy',
                'FIAM_TG_BOT_TOKEN','ANTHROPIC_API_KEY','CLAUDE_API_KEY',
                'OPENAI_API_KEY','GITHUB_TOKEN','GH_TOKEN')
foreach ($v in $dangerVars) {
    $val = [Environment]::GetEnvironmentVariable($v, "Process")
    $valUser = [Environment]::GetEnvironmentVariable($v, "User")
    $valMachine = [Environment]::GetEnvironmentVariable($v, "Machine")
    if ($val -or $valUser -or $valMachine) {
        # 脱敏：只显示前6字符
        $display = if ($val) { $val.Substring(0, [Math]::Min(6, $val.Length)) + "***" } else { "(unset)" }
        $displayU = if ($valUser) { $valUser.Substring(0, [Math]::Min(6, $valUser.Length)) + "***" } else { "(unset)" }
        $displayM = if ($valMachine) { $valMachine.Substring(0, [Math]::Min(6, $valMachine.Length)) + "***" } else { "(unset)" }
        Write-Host "${v}: Process=$display | User=$displayU | Machine=$displayM" -ForegroundColor Yellow
    }
}
Write-Host "(未列出的变量 = 不存在，安全)"

Write-Host "$sep`n[9/12] 出口 IP 检测（关键安全项）" -ForegroundColor Cyan
Write-Host "--- 直连（无代理）---"
try {
    $directIP = (Invoke-WebRequest -Uri "https://api.ipify.org?format=json" -TimeoutSec 8 -NoProxy -ErrorAction Stop).Content
    Write-Host "Direct IP: $directIP"
} catch {
    Write-Host "Direct IP: FAILED ($($_.Exception.Message))" -ForegroundColor Red
}
Write-Host "--- 走系统代理 ---"
try {
    $proxyIP = (Invoke-WebRequest -Uri "https://api.ipify.org?format=json" -TimeoutSec 8 -ErrorAction Stop).Content
    Write-Host "Proxy IP:  $proxyIP"
} catch {
    Write-Host "Proxy IP:  FAILED ($($_.Exception.Message))" -ForegroundColor Red
}
Write-Host "--- DNS 泄露检测 ---"
try {
    $dns = (Invoke-WebRequest -Uri "https://1.1.1.1/cdn-cgi/trace" -TimeoutSec 8 -ErrorAction Stop).Content
    $dns -split "`n" | Select-String "ip=|loc=|warp="
} catch {
    Write-Host "DNS trace: FAILED" -ForegroundColor Red
}

Write-Host "$sep`n[10/12] 延迟测试" -ForegroundColor Cyan
Write-Host "--- ping DO (209.38.69.231) ---"
ping 209.38.69.231 -n 3

Write-Host "--- ping ISP (99.173.22.93) ---"
ping 99.173.22.93 -n 3

Write-Host "--- 代理链路 HTTPS 延迟（经 v2rayN）---"
$proxyAddr = if ($proxyServer) { "http://$proxyServer" } else { $null }
# 测 3 个关键目标
@("https://github.com", "https://marketplace.visualstudio.com", "https://update.code.visualstudio.com") | ForEach-Object {
    $url = $_
    try {
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $r = Invoke-WebRequest -Uri $url -TimeoutSec 15 -Method Head -ErrorAction Stop -MaximumRedirection 0
        $sw.Stop()
        Write-Host "$url -> $($r.StatusCode) in $($sw.ElapsedMilliseconds)ms"
    } catch {
        if ($_.Exception.Response) {
            $sw.Stop()
            Write-Host "$url -> $($_.Exception.Response.StatusCode.value__) in $($sw.ElapsedMilliseconds)ms"
        } else {
            Write-Host "$url -> TIMEOUT/FAIL ($($_.Exception.Message))" -ForegroundColor Red
        }
    }
}

Write-Host "$sep`n[11/12] VSCode 代理配置" -ForegroundColor Cyan
$vsSettings = "$env:APPDATA\Code\User\settings.json"
if (Test-Path $vsSettings) {
    Get-Content $vsSettings | Select-String -Pattern "proxy|ssl|cert|PAC" -CaseSensitive:$false
} else {
    Write-Host "settings.json not found at $vsSettings"
}

Write-Host "$sep`n[12/12] traceroute 到 DO（看跳数和路径）" -ForegroundColor Cyan
tracert -d -h 10 -w 2000 209.38.69.231

Write-Host "$sep`n[DONE] 诊断完成，请将以上全部输出贴回" -ForegroundColor Green
