# Listens for Console + Runtime events for a few seconds and prints.
param([int]$Seconds=8,[int]$Port=9222)
$ErrorActionPreference='Stop'
$pages=Invoke-RestMethod "http://127.0.0.1:$Port/json"
$page=$pages|?{$_.type -eq 'page'}|select -First 1
if(-not $page){throw "no page"}
Add-Type -AssemblyName System.Net.WebSockets
$ws=New-Object System.Net.WebSockets.ClientWebSocket
$cts=New-Object System.Threading.CancellationTokenSource ([TimeSpan]::FromSeconds($Seconds+5))
$ws.ConnectAsync([Uri]$page.webSocketDebuggerUrl,$cts.Token).Wait()

function Send-Msg($id,$method,$params){
  $obj=@{id=$id;method=$method}
  if($params){$obj.params=$params}
  $b=[System.Text.Encoding]::UTF8.GetBytes(($obj|ConvertTo-Json -Depth 6 -Compress))
  $ws.SendAsync([System.ArraySegment[byte]]::new($b),'Text',$true,$cts.Token).Wait()
}
Send-Msg 1 'Runtime.enable' $null
Send-Msg 2 'Log.enable' $null
Send-Msg 3 'Console.enable' $null
Send-Msg 4 'Page.enable' $null
Send-Msg 5 'Page.reload' @{ignoreCache=$true}

$buf=New-Object 'byte[]' 65536
$deadline=(Get-Date).AddSeconds($Seconds)
while($ws.State -eq 'Open' -and (Get-Date) -lt $deadline){
  $sb=New-Object System.Text.StringBuilder
  $more=$true
  $rcts=New-Object System.Threading.CancellationTokenSource ([TimeSpan]::FromSeconds(2))
  try{
    while($more){
      $r=$ws.ReceiveAsync([System.ArraySegment[byte]]::new($buf),$rcts.Token).GetAwaiter().GetResult()
      $sb.Append([Text.Encoding]::UTF8.GetString($buf,0,$r.Count))|Out-Null
      $more=-not $r.EndOfMessage
    }
  }catch{continue}
  $msg=$sb.ToString()
  if($msg -match '"method":"(Runtime\.consoleAPICalled|Runtime\.exceptionThrown|Log\.entryAdded|Page\.loadEventFired|Page\.frameNavigated)"'){
    Write-Host $msg
  }
}
$ws.Dispose()
