# File-Flow unified entry (scheduled tasks / console bridge)
param(
    [switch]$Execute,   # actually move files; default is dry-run
    [switch]$DryRun,    # force dry-run (default anyway)
    [string[]]$Zone,
    [string]$Rollback,
    [switch]$AI
)
$ErrorActionPreference = "Stop"
$FF = $PSScriptRoot
$py = (Get-Command python).Source
$argsList = @("$FF\cli.py", "--ff-root", $FF)
if ($Execute -and -not $DryRun) { $argsList += "--execute" }
if ($DryRun) { $argsList += "--dry-run" }
foreach ($z in $Zone) { $argsList += @("--zone", $z) }
if ($Rollback) { $argsList += @("--rollback", $Rollback) }
if ($AI) { $argsList += "--ai" }
& $py @argsList
exit $LASTEXITCODE
