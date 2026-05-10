# scripts/cdp_eval.ps1
# Send a Runtime.evaluate to the WebView CDP socket via WebSocket and print the result.
param(
    [Parameter(Mandatory=$true)][string]$Expr,
    [int]$Port = 9222,
    [int]$TimeoutSec = 10
)
$ErrorActionPreference = 'Stop'

$pages = Invoke-RestMethod "http://127.0.0.1:$Port/json"
$page  = $pages | Where-Object { $_.type -eq 'page' } | Select-Object -First 1
if (-not $page) { throw "no page target" }
$wsUrl = $page.webSocketDebuggerUrl

Add-Type -AssemblyName System.Net.WebSockets
$ws = New-Object System.Net.WebSockets.ClientWebSocket
$cts = New-Object System.Threading.CancellationTokenSource ([TimeSpan]::FromSeconds($TimeoutSec))
$ws.ConnectAsync([Uri]$wsUrl, $cts.Token).Wait()

$msg = @{
    id = 1
    method = 'Runtime.evaluate'
    params = @{
        expression = $Expr
        returnByValue = $true
        awaitPromise = $true
        timeout = ($TimeoutSec * 1000)
    }
} | ConvertTo-Json -Depth 6 -Compress

$bytes = [System.Text.Encoding]::UTF8.GetBytes($msg)
$seg = [System.ArraySegment[byte]]::new($bytes)
$ws.SendAsync($seg, [System.Net.WebSockets.WebSocketMessageType]::Text, $true, $cts.Token).Wait()

# read until we get our id
$buf = New-Object 'byte[]' 65536
$sb = New-Object System.Text.StringBuilder
while ($ws.State -eq 'Open') {
    $sb.Clear() | Out-Null
    $more = $true
    while ($more) {
        $rseg = [System.ArraySegment[byte]]::new($buf)
        $r = $ws.ReceiveAsync($rseg, $cts.Token).GetAwaiter().GetResult()
        $sb.Append([System.Text.Encoding]::UTF8.GetString($buf, 0, $r.Count)) | Out-Null
        $more = -not $r.EndOfMessage
    }
    $obj = $sb.ToString() | ConvertFrom-Json
    if ($obj.id -eq 1) {
        $obj.result | ConvertTo-Json -Depth 8
        break
    }
}
$ws.CloseAsync('NormalClosure', 'done', $cts.Token).Wait()
