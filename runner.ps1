# File-Flow 统一入口：供计划任务与控制台桥调用
param(
    [switch]$DryRun,
    [string[]]$Zone,
    [string]$Rollback,
    [switch]$AI
)
$ErrorActionPreference = "Stop"
$FF = $PSScriptRoot
$py = (Get-Command python).Source
$argsList = @("$FF\cli.py", "--ff-root", $FF)
if ($DryRun) { $argsList += "--dry-run" }
foreach ($z in $Zone) { $argsList += @("--zone", $z) }
if ($Rollback) { $argsList += @("--rollback", $Rollback) }
if ($AI) { $argsList += "--ai" }
& $py @argsList
exit $LASTEXITCODE
