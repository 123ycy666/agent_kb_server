# 60 秒后自动重启 DSH Desktop（让 hook 挂载生效一次干净启动），并自验
Start-Sleep -Seconds 60
$log = 'D:\秘书智能体\output\restart-dsh.log'
function L($m) { "$(Get-Date -Format 'HH:mm:ss') $m" | Out-File -FilePath $log -Append -Encoding utf8 }
L 'restart begin'
Get-Process -ErrorAction SilentlyContinue | Where-Object {
  $_.ProcessName -like 'DSH*' -or ($_.Path -and $_.Path -like '*DSH Desktop*')
} | ForEach-Object { try { $_.Kill() } catch {} }
Start-Sleep -Seconds 6
Start-Process -FilePath 'D:\新建文件夹\DSH Desktop\DSH Desktop.exe'
L 'relaunched'
Start-Sleep -Seconds 90
$n = (Get-Content 'D:\秘书智能体\output\hooks\hook-log.jsonl' -ErrorAction SilentlyContinue).Count
L "hook log lines = $n"
if ($n -gt 1) { L 'RESULT: HOOK WORKS' } else { L 'RESULT: still nothing' }
