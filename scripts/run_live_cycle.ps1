param(
    [string]$Workflow = "export_lc_sc",
    [string]$Config = "D:\customs-automation\export_lc_sc.toml",
    [string]$DocumentRootBase = "D:\customs-automation\documents-live-click",
    [string]$LauncherLogRoot = "D:\customs-automation\reports\launcher_logs",
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [switch]$SkipReadiness,
    [switch]$PauseAtEnd
)

$ErrorActionPreference = "Stop"

function Write-Section {
    param([string]$Text)
    Write-Host ""
    Write-Host "=== $Text ===" -ForegroundColor Cyan
    Add-Content -Path $script:LauncherLogPath -Value ""
    Add-Content -Path $script:LauncherLogPath -Value "=== $Text ==="
}

function Write-LauncherLine {
    param(
        [string]$Text,
        [string]$Color = "Gray"
    )
    Write-Host $Text -ForegroundColor $Color
    Add-Content -Path $script:LauncherLogPath -Value $Text
}

function Normalize-LauncherText {
    param([AllowNull()][object]$Value)
    if ($null -eq $Value) {
        return ""
    }
    return [string]$Value
}

function Format-ProcessArgument {
    param([AllowNull()][string]$Argument)

    $text = Normalize-LauncherText $Argument
    if ($text -eq "") {
        return '""'
    }
    if ($text -notmatch '[\s"]') {
        return $text
    }

    $escaped = $text -replace '(\\*)"', '$1$1\"'
    $escaped = $escaped -replace '(\\+)$', '$1$1'
    return '"' + $escaped + '"'
}

function Get-JsonFromCommandOutput {
    param([AllowNull()][string]$Text)

    $Text = Normalize-LauncherText $Text
    if (-not $Text) {
        throw "Command output did not contain a parseable JSON payload."
    }

    $candidateIndexes = @()
    for ($i = 0; $i -lt $Text.Length; $i++) {
        if ($Text[$i] -eq "{") {
            $candidateIndexes += $i
        }
    }
    [array]::Reverse($candidateIndexes)

    foreach ($jsonStart in $candidateIndexes) {
        $jsonText = $Text.Substring($jsonStart)
        try {
            return $jsonText | ConvertFrom-Json
        }
        catch {
            continue
        }
    }

    throw "Command output did not contain a parseable JSON payload."
}

function Invoke-ProjectJsonCommand {
    param(
        [string[]]$Arguments,
        [switch]$AllowFailure
    )

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "uv"
    $psi.Arguments = (($Arguments | ForEach-Object { Format-ProcessArgument $_ }) -join " ")
    $psi.WorkingDirectory = $RepoRoot
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi
    [void]$process.Start()
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()

    $exitCode = $process.ExitCode
    $outputSegments = @()
    $stdoutText = Normalize-LauncherText $stdout
    $stderrText = Normalize-LauncherText $stderr
    if ($stderrText.Trim()) {
        $outputSegments += $stderrText.Trim()
    }
    if ($stdoutText.Trim()) {
        $outputSegments += $stdoutText.Trim()
    }
    $joinedOutput = Normalize-LauncherText ($outputSegments -join "`n")
    $outputText = $joinedOutput.Trim()

    if ($outputText) {
        Write-Host $outputText
        Add-Content -Path $script:LauncherLogPath -Value $outputText
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
$documentRoot = $DocumentRootBase
New-Item -ItemType Directory -Force -Path $documentRoot | Out-Null
New-Item -ItemType Directory -Force -Path $LauncherLogRoot | Out-Null
$script:LauncherLogPath = Join-Path $LauncherLogRoot ("{0}.{1}.log" -f $Workflow, $timestamp)
New-Item -ItemType File -Force -Path $script:LauncherLogPath | Out-Null

Write-LauncherLine "Workflow: $Workflow" "Green"
Write-LauncherLine "Config: $Config" "Green"
Write-LauncherLine "Document root: $documentRoot" "Green"
Write-LauncherLine "Launcher log: $script:LauncherLogPath" "Green"

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
    $stagedWriteOperationCount = [int]$validate.Json.staged_write_operation_count

    Write-Host ""
    Write-Host "Run ID: $runId" -ForegroundColor Green

    if ($hardBlockCount -gt 0) {
        Write-Section "Failure Explanation"
        $explanation = Invoke-ProjectJsonCommand -Arguments @(
            "run", "python", "-m", "project",
            "explain-run-failure", $Workflow,
            "--config", $Config,
            "--run-id", $runId
        )
    }

    if ($writePhaseStatus -notin @("committed", "not_started")) {
        Write-Host "Write phase stopped at '$writePhaseStatus'. Stopping before print." -ForegroundColor Yellow
        Write-Host "Explain the exact cause with:" -ForegroundColor Yellow
        Write-Host "uv run python -m project explain-run-failure $Workflow --config `"$Config`" --run-id `"$runId`"" -ForegroundColor Yellow
        Write-Host "Check full status with:" -ForegroundColor Yellow
        Write-Host "uv run python -m project report-run-status $Workflow --config `"$Config`" --run-id `"$runId`"" -ForegroundColor Yellow
        Finish-Script 1
    }

    if ($hardBlockCount -gt 0) {
        Write-Host "Validation produced $hardBlockCount hard block(s), but eligible mails will continue through the live cycle." -ForegroundColor Yellow
        Write-Host "Blocked mails will stay out of downstream print/mail-move handling for this run." -ForegroundColor Yellow
    }

    if ($stagedWriteOperationCount -eq 0) {
        Write-Host "No workbook writes were staged; skipping print planning and print execution." -ForegroundColor Yellow

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
        Write-Host "Live cycle completed without workbook writes." -ForegroundColor Green
        Write-Host "Run ID: $runId" -ForegroundColor Green
        Write-Host "Write: $($status.Json.manual_verification.write_phase_status)" -ForegroundColor Green
        Write-Host "Print: $($status.Json.manual_verification.print_phase_status)" -ForegroundColor Green
        Write-Host "Mail move: $($status.Json.manual_verification.mail_move_phase_status)" -ForegroundColor Green
        Finish-Script 0
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
catch {
    Write-Host ""
    Write-Host "Launcher error: $($_.Exception.Message)" -ForegroundColor Red
    Add-Content -Path $script:LauncherLogPath -Value ""
    Add-Content -Path $script:LauncherLogPath -Value "Launcher error: $($_.Exception.Message)"
    Add-Content -Path $script:LauncherLogPath -Value "Script stack trace:"
    Add-Content -Path $script:LauncherLogPath -Value ($_ | Out-String)

    if ($runId) {
        Write-Host "Latest run id: $runId" -ForegroundColor Yellow
        Write-Host "Check status with:" -ForegroundColor Yellow
        Write-Host "uv run python -m project report-run-status $Workflow --config `"$Config`" --run-id `"$runId`"" -ForegroundColor Yellow
        Add-Content -Path $script:LauncherLogPath -Value "Latest run id: $runId"
    }

    Write-Host "Launcher log: $script:LauncherLogPath" -ForegroundColor Yellow
    Finish-Script 1
}
finally {
    Pop-Location
}

Finish-Script 0
