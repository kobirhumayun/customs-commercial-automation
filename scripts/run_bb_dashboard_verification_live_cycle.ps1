param(
    [string]$Config = "D:\customs-automation\workflow.toml",
    [string]$LauncherLogRoot = "D:\customs-automation\reports\launcher_logs",
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [switch]$SkipReadiness,
    [switch]$PauseAtEnd
)

$sharedRunner = Join-Path $PSScriptRoot "run_live_cycle.ps1"
$arguments = @{
    Workflow = "bb_dashboard_verification"
    Config = $Config
    LauncherLogRoot = $LauncherLogRoot
    RepoRoot = $RepoRoot
}

if ($SkipReadiness) {
    $arguments["SkipReadiness"] = $true
}
if ($PauseAtEnd) {
    $arguments["PauseAtEnd"] = $true
}

& $sharedRunner @arguments
exit $LASTEXITCODE
