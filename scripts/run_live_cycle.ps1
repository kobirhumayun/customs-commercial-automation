param(
    [string]$Workflow = "export_lc_sc",
    [string]$Config = "D:\customs-automation\export_lc_sc.toml",
    [string]$DocumentRootBase = "D:\customs-automation\documents-live-click",
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [switch]$SkipReadiness,
    [switch]$PauseAtEnd
)

$ErrorActionPreference = "Stop"

function Write-Section {
    param([string]$Text)
    Write-Host ""
    Write-Host "=== $Text ===" -ForegroundColor Cyan
}

function Get-JsonFromCommandOutput {
    param([string]$Text)

    $jsonStart = $Text.IndexOf("{")
    if ($jsonStart -lt 0) {
        throw "Command output did not contain JSON."
    }
    $jsonText = $Text.Substring($jsonStart)
    return $jsonText | ConvertFrom-Json
}

function Invoke-ProjectJsonCommand {
    param(
        [string[]]$Arguments,
        [switch]$AllowFailure
    )

    $outputLines = & uv @Arguments 2>&1 | ForEach-Object { $_.ToString() }
    $exitCode = $LASTEXITCODE
    $outputText = ($outputLines -join "`n").Trim()

    if ($outputText) {
        Write-Host $outputText
    }

    if (-not $AllowFailure -and $exitCode -ne 0) {
        throw "Command failed with exit code $exitCode."
    }

    return [pscustomobject]@{
        ExitCode = $exitCode
        Output = $outputText
        Json = if ($outputText) { Get-JsonFromCommandOutput -Text $outputText } else { $null }
    }
}

function Finish-Script {
    param([int]$ExitCode = 0)
    if ($PauseAtEnd) {
        Write-Host ""
        Read-Host "Press Enter to close"
    }
    exit $ExitCode
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$documentRoot = Join-Path $DocumentRootBase $timestamp
New-Item -ItemType Directory -Force -Path $documentRoot | Out-Null

Write-Host "Workflow: $Workflow" -ForegroundColor Green
Write-Host "Config: $Config" -ForegroundColor Green
Write-Host "Document root: $documentRoot" -ForegroundColor Green

Push-Location $RepoRoot
try {
    if (-not $SkipReadiness) {
        Write-Section "Live Readiness"
        $readiness = Invoke-ProjectJsonCommand -Arguments @(
            "run", "python", "-m", "project",
            "report-live-readiness", $Workflow,
            "--config", $Config
        )
        if ($readiness.Json.overall_status -ne "ready") {
            Write-Host ""
            Write-Host "Readiness did not return 'ready'. Stopping before any live mutation." -ForegroundColor Yellow
            Finish-Script 1
        }
    }

    Write-Section "Validate Run"
    $validate = Invoke-ProjectJsonCommand -Arguments @(
        "run", "python", "-m", "project",
        "validate-run", $Workflow,
        "--config", $Config,
        "--live-outlook-snapshot",
        "--live-erp",
        "--live-workbook",
        "--document-root", $documentRoot,
        "--apply-live-writes"
    )
    $runId = [string]$validate.Json.run_id
    $writePhaseStatus = [string]$validate.Json.write_phase_status
    $hardBlockCount = [int]$validate.Json.summary.hard_block

    Write-Host ""
    Write-Host "Run ID: $runId" -ForegroundColor Green

    if ($hardBlockCount -gt 0) {
        Write-Host "Validation produced hard blocks. Stopping." -ForegroundColor Yellow
        Write-Host "Check status with:" -ForegroundColor Yellow
        Write-Host "uv run python -m project report-run-status $Workflow --config `"$Config`" --run-id `"$runId`"" -ForegroundColor Yellow
        Finish-Script 1
    }

    if ($writePhaseStatus -notin @("committed", "not_started")) {
        Write-Host "Write phase stopped at '$writePhaseStatus'. Stopping before print." -ForegroundColor Yellow
        Write-Host "Check status with:" -ForegroundColor Yellow
        Write-Host "uv run python -m project report-run-status $Workflow --config `"$Config`" --run-id `"$runId`"" -ForegroundColor Yellow
        Finish-Script 1
    }

    Write-Section "Plan Print"
    $plan = Invoke-ProjectJsonCommand -Arguments @(
        "run", "python", "-m", "project",
        "plan-print", $Workflow,
        "--config", $Config,
        "--run-id", $runId
    )

    Write-Section "Execute Print"
    $print = Invoke-ProjectJsonCommand -Arguments @(
        "run", "python", "-m", "project",
        "execute-print", $Workflow,
        "--config", $Config,
        "--run-id", $runId,
        "--live-print"
    )
    $printPhaseStatus = [string]$print.Json.print_phase_status

    if ($printPhaseStatus -ne "completed") {
        Write-Host ""
        Write-Host "Print did not complete cleanly. Mail moves were not attempted." -ForegroundColor Yellow
        Write-Host "Run ID: $runId" -ForegroundColor Yellow
        if ($printPhaseStatus -eq "uncertain_incomplete") {
            Write-Host "If paper already printed, use:" -ForegroundColor Yellow
            Write-Host "uv run python -m project acknowledge-partial-print $Workflow --config `"$Config`" --run-id `"$runId`" --printed-count <N>" -ForegroundColor Yellow
            Write-Host "Then rerun:" -ForegroundColor Yellow
            Write-Host "uv run python -m project execute-print $Workflow --config `"$Config`" --run-id `"$runId`" --live-print" -ForegroundColor Yellow
        } else {
            Write-Host "Check status with:" -ForegroundColor Yellow
            Write-Host "uv run python -m project report-run-status $Workflow --config `"$Config`" --run-id `"$runId`"" -ForegroundColor Yellow
        }
        Finish-Script 1
    }

    Write-Section "Execute Mail Moves"
    $mailMove = Invoke-ProjectJsonCommand -Arguments @(
        "run", "python", "-m", "project",
        "execute-mail-moves", $Workflow,
        "--config", $Config,
        "--run-id", $runId,
        "--live-outlook"
    )

    Write-Section "Final Status"
    $status = Invoke-ProjectJsonCommand -Arguments @(
        "run", "python", "-m", "project",
        "report-run-status", $Workflow,
        "--config", $Config,
        "--run-id", $runId
    )

    Write-Host ""
    Write-Host "Live cycle completed." -ForegroundColor Green
    Write-Host "Run ID: $runId" -ForegroundColor Green
    Write-Host "Write: $($status.Json.manual_verification.write_phase_status)" -ForegroundColor Green
    Write-Host "Print: $($status.Json.manual_verification.print_phase_status)" -ForegroundColor Green
    Write-Host "Mail move: $($status.Json.manual_verification.mail_move_phase_status)" -ForegroundColor Green
}
finally {
    Pop-Location
}

Finish-Script 0
