#!/usr/bin/env pwsh
# diag_vscode.ps1 — 针对 VSCode 扩展加载问题的精确排查
# iris 下运行: powershell -ExecutionPolicy Bypass -File F:\fiam-code\scripts\diag_vscode.ps1

$sep = "`n" + ("=" * 60)

Write-Host "$sep`n[1] VSCode 进程数量" -ForegroundColor Cyan
$codeProcs = Get-Process -Name "Code" -ErrorAction SilentlyContinue
Write-Host "Code 进程数: $($codeProcs.Count)"
if ($codeProcs.Count -gt 0) {
    Write-Host "最早启动: $($codeProcs | Sort-Object StartTime | Select-Object -First 1 -ExpandProperty StartTime)"
    Write-Host "请确保改完 settings.json 后完整重启过 VSCode（关闭所有窗口再开）"
}

Write-Host "$sep`n[2] VSCode settings.json 完整代理部分" -ForegroundColor Cyan
$vsSettings = "$env:APPDATA\Code\User\settings.json"
if (Test-Path $vsSettings) {
    $content = Get-Content $vsSettings -Raw
    # 显示所有 proxy/ssl/network 相关行
    Get-Content $vsSettings | ForEach-Object {
        if ($_ -match 'proxy|ssl|cert|network|timeout|fetch|marketplace|gallery|update') {
            Write-Host $_.Trim()
        }
    }
    Write-Host "`n--- 完整 settings.json (如果不大) ---"
    if ($content.Length -lt 5000) {
        Write-Host $content
    } else {
        Write-Host "(文件太大，仅显示关键行)"
    }
} else {
    Write-Host "settings.json NOT FOUND" -ForegroundColor Red
}

Write-Host "$sep`n[3] 模拟 VSCode 扩展市场 API 请求" -ForegroundColor Cyan
# VSCode 用这个 API 查扩展
$marketUrl = "https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery"
$body = '{"filters":[{"criteria":[{"filterType":7,"value":"ms-python.python"}]}],"flags":870}'
try {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $resp = Invoke-WebRequest -Uri $marketUrl -Method POST -Body $body `
        -ContentType "application/json" `
        -Headers @{"Accept"="application/json;api-version=6.1-preview.1"} `
        -UseBasicParsing -TimeoutSec 30
    $sw.Stop()
    Write-Host "Marketplace API: HTTP $($resp.StatusCode) in $($sw.ElapsedMilliseconds)ms" -ForegroundColor Green
    Write-Host "Response size: $($resp.Content.Length) bytes"
} catch {
    Write-Host "Marketplace API FAILED: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host "$sep`n[4] 模拟扩展 VSIX 下载" -ForegroundColor Cyan
$vsixUrl = "https://marketplace.visualstudio.com/_apis/public/gallery/publishers/ms-python/vsextensions/python/latest/vspackage"
try {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $resp = Invoke-WebRequest -Uri $vsixUrl -Method Head -UseBasicParsing -TimeoutSec 30 -MaximumRedirection 0
    $sw.Stop()
    Write-Host "VSIX head: HTTP $($resp.StatusCode) in $($sw.ElapsedMilliseconds)ms"
} catch {
    if ($_.Exception.Response) {
        $sw.Stop()
        $status = [int]$_.Exception.Response.StatusCode
        Write-Host "VSIX head: HTTP $status in $($sw.ElapsedMilliseconds)ms (redirect expected)"
    } else {
        Write-Host "VSIX head FAILED: $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Host "$sep`n[5] DNS 解析测试（本地 vs 远端）" -ForegroundColor Cyan
@("marketplace.visualstudio.com", "github.com", "update.code.visualstudio.com", 
  "az764295.vo.msecnd.net", "download.visualstudio.microsoft.com") | ForEach-Object {
    try {
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $r = Resolve-DnsName $_ -Type A -DnsOnly -ErrorAction Stop
        $sw.Stop()
        $ips = ($r | Where-Object { $_.QueryType -eq 'A' } | Select-Object -ExpandProperty IPAddress) -join ", "
        Write-Host "$_ -> $ips ($($sw.ElapsedMilliseconds)ms)"
    } catch {
        Write-Host "$_ -> FAILED ($($_.Exception.Message))" -ForegroundColor Red
    }
}

Write-Host "$sep`n[6] 直连测试（绕过代理）" -ForegroundColor Cyan
Write-Host "测试这些域名是否不走代理也能连（如果能 = 有直连泄露风险）"
@("https://marketplace.visualstudio.com", "https://github.com") | ForEach-Object {
    try {
        # PowerShell 5 没有 -NoProxy，用 WebClient 绕过
        $wc = New-Object System.Net.WebClient
        $wc.Proxy = [System.Net.GlobalProxySelection]::GetEmptyWebProxy()
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $result = $wc.DownloadString("https://api.ipify.org")
        $sw.Stop()
        Write-Host "Direct exit IP: $result ($($sw.ElapsedMilliseconds)ms)" -ForegroundColor Yellow
        $wc.Dispose()
        break  # 只需要测一次
    } catch {
        Write-Host "Direct connection: BLOCKED/TIMEOUT (安全)" -ForegroundColor Green
    }
}

Write-Host "$sep`n[7] v2rayN 本地端口协议检测" -ForegroundColor Cyan
# 测试 10808 是纯 SOCKS5 还是 HTTP 兼容
try {
    # HTTP CONNECT 测试
    $tcp = New-Object System.Net.Sockets.TcpClient
    $tcp.Connect("127.0.0.1", 10808)
    $stream = $tcp.GetStream()
    $writer = New-Object System.IO.StreamWriter($stream)
    $reader = New-Object System.IO.StreamReader($stream)
    $writer.Write("CONNECT api.ipify.org:443 HTTP/1.1`r`nHost: api.ipify.org:443`r`n`r`n")
    $writer.Flush()
    $stream.ReadTimeout = 5000
    $line = $reader.ReadLine()
    Write-Host "HTTP CONNECT response: $line"
    if ($line -match "200") {
        Write-Host "端口 10808 支持 HTTP CONNECT ✓" -ForegroundColor Green
    } else {
        Write-Host "端口 10808 可能不支持 HTTP，尝试 socks5://127.0.0.1:10808" -ForegroundColor Yellow
    }
    $tcp.Close()
} catch {
    Write-Host "HTTP CONNECT test failed: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "可能是纯 SOCKS5 端口，VSCode 需要改为 socks5://127.0.0.1:10808" -ForegroundColor Yellow
}

Write-Host "$sep`n[DONE] 结果贴回" -ForegroundColor Green
