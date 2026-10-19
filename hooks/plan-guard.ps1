# plan-guard.ps1 —— PreToolUse 硬门：动代码前必须有一份"近期"的计划文件
#
# 设计依据：
#   - dsh-hooks 手册 §6.7：决策只能写 stdout JSON；exit code 不 gate；脚本放工作区内、进程内执行。
#   - 手册 §2.2：DSH 文件工具是 write/edit，入参可能是 file_path；
#   - 社区教训：路径匹配必须考虑相对路径/大小写/符号链接，否则能被绕过。
#
# 可配置（<项目>/.dsh/plan-guard.json，缺省用下面的默认值）：
#   planDir          计划目录，默认 docs/plans
#   maxPlanAgeHours  计划"够新"的小时数，默认 24
#   gateGlobs        要拦的文件（-like 通配），默认常见代码/配置文件
#   exemptGlobs      永远放行的路径通配，默认 docs/ output/ hooks/ .dsh/ 和所有 .md
#   planGlobs        什么算计划文件，默认 *.md
#
# 判定：工具∈write/edit 且目标命中 gateGlobs 且不命中 exemptGlobs 且没有"够新"的计划 → deny。
# 其余一律放行。脚本自身出错也放行（fail-open），原因写 stderr 供插件日志查。

$ErrorActionPreference = 'Stop'

$raw = ''
try {
  # 必须按字节读再显式解码 UTF-8：用 [Console]::In 会按控制台编码(GBK)解码，
  # 遇到中文路径 -> JSON 解析抛异常 -> 静默放行（实测踩过）。
  $stdin = [Console]::OpenStandardInput()
  $ms = New-Object System.IO.MemoryStream
  $stdin.CopyTo($ms)
  $raw = [System.Text.Encoding]::UTF8.GetString($ms.ToArray())
} catch { exit 0 }
if ([string]::IsNullOrWhiteSpace($raw)) { exit 0 }

$j = $null
try { $j = $raw | ConvertFrom-Json } catch { exit 0 }

$evt = [string]$j.hook_event_name
if ($evt -and $evt -ne 'PreToolUse') { exit 0 }

$tool = ([string]$j.tool_name).ToLowerInvariant()
if ($tool -ne 'write' -and $tool -ne 'edit') { exit 0 }

$cwd = [string]$j.cwd
if (-not $cwd) { $cwd = (Get-Location).Path }

# ── 取目标路径：不同工具不同字段，挨个试 ─────────────────────────────
$target = ''
if ($j.tool_input) {
  foreach ($k in 'file_path', 'path', 'filePath', 'target_file') {
    $v = $j.tool_input.$k
    if ($v) { $target = [string]$v; break }
  }
}
if (-not $target) { exit 0 }

function Write-Utf8([string]$text) {
  $enc = New-Object System.Text.UTF8Encoding($false)
  $bytes = $enc.GetBytes($text + "`n")
  $stdout = [Console]::OpenStandardOutput()
  $stdout.Write($bytes, 0, $bytes.Length)
  $stdout.Flush()
}

# ── 配置 ────────────────────────────────────────────────────────────
$cfg = @{
  planDir         = 'docs/plans'
  maxPlanAgeHours = 24
  gateGlobs       = @('*.py', '*.ts', '*.tsx', '*.js', '*.mjs', '*.vue', '*.sql', '*.ps1', '*.sh', '*.bat', '*.yml', '*.yaml', '*.json')
  exemptGlobs     = @('docs/*', 'output/*', 'hooks/*', '.dsh/*', '*.md')
  planGlobs       = @('*.md')
}
foreach ($c in @((Join-Path $cwd '.dsh\plan-guard.json'), (Join-Path $cwd 'plan-guard.json'))) {
  if (Test-Path $c) {
    try {
      $u = Get-Content $c -Raw | ConvertFrom-Json
      foreach ($k in 'planDir', 'maxPlanAgeHours', 'gateGlobs', 'exemptGlobs', 'planGlobs') {
        if ($u.PSObject.Properties.Name -contains $k -and $u.$k) { $cfg[$k] = $u.$k }
      }
    } catch { }
    break
  }
}

# ── 路径归一化：相对→绝对、正斜杠 ───────────────────────────────────
$abs = $target
if (-not [System.IO.Path]::IsPathRooted($abs)) { $abs = Join-Path $cwd $abs }
try { $abs = [System.IO.Path]::GetFullPath($abs) } catch { }
$rel = $abs
if ($abs.StartsWith($cwd, [StringComparison]::OrdinalIgnoreCase)) {
  $rel = $abs.Substring($cwd.Length).TrimStart('\', '/')
}
$relF = ($rel -replace '\\', '/')

function Hit([object]$globs, [string]$p) {
  foreach ($g in @($globs)) { if ($p -like $g) { return $true } }
  return $false
}

if (Hit $cfg.exemptGlobs $relF) { exit 0 }
if (-not (Hit $cfg.gateGlobs $relF)) { exit 0 }

# ── 找"够新"的计划文件 ──────────────────────────────────────────────
$planDirPath = Join-Path $cwd ([string]$cfg.planDir)
$cutoff = (Get-Date).AddHours(-[double]$cfg.maxPlanAgeHours)
$plan = $null
if (Test-Path $planDirPath) {
  $plan = Get-ChildItem $planDirPath -File -ErrorAction SilentlyContinue |
    Where-Object { (Hit $cfg.planGlobs $_.Name) -and $_.LastWriteTime -gt $cutoff } |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
}
if ($plan) { exit 0 }

$reason = '动代码前先写计划：在 ' + $cfg.planDir + '/ 放一份计划（目标 / 验收判据 / 步骤→verify），' + $cfg.maxPlanAgeHours + ' 小时内更新过即可。没有就先写计划再改文件：' + $relF
$json = '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"' + ($reason -replace '\\', '\\' -replace '"', '\"') + '"}}'
Write-Utf8 $json
exit 0
