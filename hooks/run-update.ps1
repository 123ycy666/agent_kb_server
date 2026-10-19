# 2026-10-19 DSH Desktop 2.0.18 静默更新 + 更新后自动挂 hook + 重启应用
# 由 agent 生成并以"脱离父进程"的方式启动：应用被关闭时本脚本仍继续跑。
$ErrorActionPreference = 'Continue'
$log = 'D:\秘书智能体\output\update-2.0.18.log'
function L($m) { "$(Get-Date -Format 'HH:mm:ss') $m" | Out-File -FilePath $log -Append -Encoding utf8 }
L '=== start ==='
Start-Sleep -Seconds 15   # 给用户时间看到提示

$exe = 'D:\秘书智能体\output\dl\DSH-Desktop-2.0.18-x64-Setup.exe'
$dsh = 'C:\Users\Fred\.dsh'
$patch = "$dsh\profiles\desktop\cordis.patch.yml"
$nested = "$dsh\profiles\desktop\node_modules\@deepseek-ai\dsh\node_modules\@deepseek-ai\dsh-hooks-claude-code"
$link = "$dsh\profiles\desktop\node_modules\@deepseek-ai\dsh-hooks-claude-code"
$appdir = 'D:\新建文件夹\DSH Desktop'

# 1) 关掉所有 DSH 进程（安装需要文件不被占用）
Get-Process -ErrorAction SilentlyContinue | Where-Object {
  $_.ProcessName -like 'DSH*' -or ($_.Path -and $_.Path -like '*DSH Desktop*')
} | ForEach-Object {
  L "kill $($_.ProcessName) pid=$($_.Id)"
  try { $_.Kill() } catch { L "kill failed: $($_.Exception.Message)" }
}
Start-Sleep -Seconds 6

# 2) 静默安装
L "install: $exe"
try {
  $p = Start-Process -FilePath $exe -ArgumentList '/S' -PassThru -Wait
  L "installer exit=$($p.ExitCode)"
} catch { L "installer failed: $($_.Exception.Message)" }
Start-Sleep -Seconds 8

# 3) 版本核对
try { $v = (& dsh --version 2>&1 | Out-String).Trim(); L "dsh --version => $v" } catch { L "version check failed: $($_.Exception.Message)" }

# 4) 若新 profile 自带 hook 桥接包，则挂上（junction + patch insert）
$mounted = $false
if (Test-Path "$nested\lib\index.js") {
  L 'bridge package found in new profile'
  if (-not (Test-Path $link)) { cmd /c rmdir $link 2>$null | Out-Null; New-Item -ItemType Junction -Path $link -Target $nested | Out-Null }
  L "junction => $(Test-Path "$link\lib\index.js")"
  $raw = Get-Content $patch -Raw
  if ($raw -notmatch 'hooks-observe') {
    Copy-Item $patch "$patch.bak-before-mount" -Force
    $block = @"

# ── hook 观测实验（2.0.18 更新后自动添加，只记录不拦截）──────────────
- insert:
    - id: hooks-observe
      name: '@deepseek-ai/dsh-hooks-claude-code'
      config:
        configPath: 'D:\秘书智能体\hooks\hooks.json'
        defaultTimeoutMs: 30000
"@
    Add-Content -Path $patch -Value $block -Encoding utf8
    L 'patch insert appended'
  } else { L 'patch already had hooks-observe' }
  try {
    $dump = (& dsh --profile desktop --dump-config 2>&1 | Out-String)
    L ("dump-config lines=" + ($dump -split "`n").Count + " has-observe=" + ($dump -match 'hooks-observe'))
  } catch { L "dump failed: $($_.Exception.Message)" }
  $mounted = $true
} else {
  L 'bridge package NOT found in new profile -> skip mount'
}

# 5) 重新启动应用
$appexe = Get-ChildItem "$appdir\*.exe" -ErrorAction SilentlyContinue | Where-Object { $_.Name -notlike '*ninstall*' } | Select-Object -First 1
if ($appexe) {
  L "launch $($appexe.FullName)"
  Start-Process -FilePath $appexe.FullName
} else { L 'app exe not found!' }

# 6) 等应用起来，验证 hook 是否真的触发
if ($mounted) {
  Start-Sleep -Seconds 90
  $hl = 'D:\秘书智能体\output\hooks\hook-log.jsonl'
  $n = (Get-Content $hl -ErrorAction SilentlyContinue).Count
  L "hook log lines after relaunch = $n"
  if ($n -gt 1) { L 'RESULT: HOOK WORKS' } else { L 'RESULT: still no hook events' }
}
L '=== done ==='
