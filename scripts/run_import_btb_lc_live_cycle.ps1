param(
    [ValidateSet("current_full", "file_picker")]
    [string]$LauncherPath = "current_full",
    [string]$Config = "D:\customs-automation\workflow.toml",
    [string]$ImportDocumentRoot = "",
    [string]$OutputDirectory = "",
    [string]$LauncherLogRoot = "D:\customs-automation\reports\launcher_logs",
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [string[]]$InputPath = @(),
    [switch]$NoOpenReport,
    [switch]$PreviewCommand,
    [switch]$PauseAtEnd
)

$ErrorActionPreference = "Stop"

function Normalize-LauncherText {
    param([AllowNull()][object]$Value)
    if ($null -eq $Value) {
        return ""
    }
    return [string]$Value
}

function Write-LauncherLine {
    param(
        [string]$Text,
        [string]$Color = "Gray"
    )
    Write-Host $Text -ForegroundColor $Color
    Add-Content -Path $script:LauncherLogPath -Value $Text
}

function Write-Section {
    param([string]$Text)
    Write-Host ""
    Write-Host "=== $Text ===" -ForegroundColor Cyan
    Add-Content -Path $script:LauncherLogPath -Value ""
    Add-Content -Path $script:LauncherLogPath -Value "=== $Text ==="
}

function Finish-Script {
    param([int]$ExitCode = 0)
    if ($PauseAtEnd) {
        Write-Host ""
        Read-Host "Press Enter to close"
    }
    exit $ExitCode
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
    param([string[]]$Arguments)

    $argumentText = (($Arguments | ForEach-Object { Format-ProcessArgument $_ }) -join " ")
    Write-LauncherLine "uv $argumentText" "DarkGray"
    if ($PreviewCommand) {
        return [pscustomobject]@{
            ExitCode = 0
            Output = ""
            Json = $null
        }
    }

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "uv"
    $psi.Arguments = $argumentText
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

    $outputSegments = @()
    $stderrText = Normalize-LauncherText $stderr
    $stdoutText = Normalize-LauncherText $stdout
    if ($stderrText.Trim()) {
        $outputSegments += $stderrText.Trim()
    }
    if ($stdoutText.Trim()) {
        $outputSegments += $stdoutText.Trim()
    }
    $outputText = (Normalize-LauncherText ($outputSegments -join "`n")).Trim()

    if ($outputText) {
        Write-Host $outputText
        Add-Content -Path $script:LauncherLogPath -Value $outputText
    }
    if ($process.ExitCode -ne 0) {
        throw "Command failed with exit code $($process.ExitCode)."
    }

    return [pscustomobject]@{
        ExitCode = $process.ExitCode
        Output = $outputText
        Json = if ($outputText) { Get-JsonFromCommandOutput -Text $outputText } else { $null }
    }
}

function Get-TomlStringValue {
    param(
        [string]$Path,
        [string]$Key
    )

    $pattern = '^\s*' + [regex]::Escape($Key) + '\s*=\s*"(.*)"\s*(?:#.*)?$'
    foreach ($line in Get-Content -Path $Path) {
        $match = [regex]::Match($line, $pattern)
        if ($match.Success) {
            return ($match.Groups[1].Value -replace '\\"', '"')
        }
    }
    return ""
}

function Resolve-TemplatePath {
    param([string]$Template)

    $year = (Get-Date).Year.ToString()
    return $Template.Replace("{year}", $year).Replace("{workflow_id}", "import_btb_lc")
}

function Test-PathUnderRoot {
    param(
        [string]$Path,
        [string]$Root
    )

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $fullRoot = [System.IO.Path]::GetFullPath($Root)
    if (-not $fullRoot.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
        $fullRoot += [System.IO.Path]::DirectorySeparatorChar
    }
    return $fullPath.StartsWith($fullRoot, [System.StringComparison]::OrdinalIgnoreCase)
}

function Select-ImportPdfFiles {
    param([string]$InitialDirectory)

    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.Application]::EnableVisualStyles()
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Title = "Select stored Import BTB LC PDF file(s)"
    $dialog.InitialDirectory = $InitialDirectory
    $dialog.Filter = "PDF files (*.pdf)|*.pdf"
    $dialog.Multiselect = $true
    $dialog.CheckFileExists = $true
    $dialog.CheckPathExists = $true
    $result = $dialog.ShowDialog()
    if ($result -ne [System.Windows.Forms.DialogResult]::OK) {
        return @()
    }
    return @($dialog.FileNames)
}

if (-not (Test-Path -LiteralPath $Config -PathType Leaf)) {
    Write-Host "Config file not found: $Config" -ForegroundColor Red
    Finish-Script 1
}
if (-not (Test-Path -LiteralPath $RepoRoot -PathType Container)) {
    Write-Host "Repo root not found: $RepoRoot" -ForegroundColor Red
    Finish-Script 1
}

$configReportRoot = Get-TomlStringValue -Path $Config -Key "report_root"
if (-not $configReportRoot) {
    $configReportRoot = "D:\customs-automation\reports"
}
$configImportRoot = Get-TomlStringValue -Path $Config -Key "import_document_root"
if (-not $ImportDocumentRoot) {
    $ImportDocumentRoot = if ($configImportRoot) { $configImportRoot } else { "D:\customs-automation\import-documents" }
}
if (-not $OutputDirectory) {
    $reportFolderName = if ($LauncherPath -eq "current_full") { "BTB LC Current Live" } else { "BTB LC File Picker Live" }
    $OutputDirectory = Join-Path $configReportRoot $reportFolderName
}

New-Item -ItemType Directory -Force -Path $ImportDocumentRoot | Out-Null
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
New-Item -ItemType Directory -Force -Path $LauncherLogRoot | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$script:LauncherLogPath = Join-Path $LauncherLogRoot ("import_btb_lc.{0}.{1}.log" -f $LauncherPath, $timestamp)
New-Item -ItemType File -Force -Path $script:LauncherLogPath | Out-Null

Write-LauncherLine "Workflow: import_btb_lc" "Green"
Write-LauncherLine "Launcher path: $LauncherPath" "Green"
Write-LauncherLine "Config: $Config" "Green"
Write-LauncherLine "Import document root: $ImportDocumentRoot" "Green"
Write-LauncherLine "Output directory: $OutputDirectory" "Green"
Write-LauncherLine "Launcher log: $script:LauncherLogPath" "Green"

Push-Location $RepoRoot
try {
    if ($LauncherPath -eq "current_full") {
        Write-Section "Current Full Live Run"
        $arguments = @(
            "run", "python", "-m", "project",
            "run-import-btb-lc-current",
            "--config", $Config,
            "--live-outlook-snapshot",
            "--output", $OutputDirectory,
            "--import-document-root", $ImportDocumentRoot,
            "--apply-live-writes",
            "--move-mails",
            "--live-mail-moves"
        )
    } else {
        Write-Section "Select Stored PDFs"
        $selectedPaths = @($InputPath)
        if ($selectedPaths.Count -eq 0) {
            $selectedPaths = Select-ImportPdfFiles -InitialDirectory $ImportDocumentRoot
        }
        if ($selectedPaths.Count -eq 0) {
            Write-LauncherLine "No PDF files selected. Nothing to do." "Yellow"
            Finish-Script 1
        }
        foreach ($selectedPath in $selectedPaths) {
            if (-not (Test-Path -LiteralPath $selectedPath -PathType Leaf)) {
                throw "Selected file does not exist: $selectedPath"
            }
            if ([System.IO.Path]::GetExtension($selectedPath).ToLowerInvariant() -ne ".pdf") {
                throw "Selected file is not a PDF: $selectedPath"
            }
            if (-not (Test-PathUnderRoot -Path $selectedPath -Root $ImportDocumentRoot)) {
                throw "Selected PDF must be beneath import document root. File: $selectedPath; root: $ImportDocumentRoot"
            }
            Write-LauncherLine "Selected: $selectedPath" "Gray"
        }

        $workbookTemplate = Get-TomlStringValue -Path $Config -Key "master_workbook_path_template"
        if (-not $workbookTemplate) {
            throw "master_workbook_path_template is required in $Config for the File Picker launcher."
        }
        $workbookPath = Resolve-TemplatePath -Template $workbookTemplate
        if (-not (Test-Path -LiteralPath $workbookPath -PathType Leaf)) {
            throw "Workbook path from config does not exist: $workbookPath"
        }
        Write-LauncherLine "Workbook: $workbookPath" "Green"

        Write-Section "File Picker Live Run"
        $arguments = @(
            "run", "python", "-m", "project",
            "run-import-btb-lc-file-picker",
            "--output", $OutputDirectory,
            "--workbook", $workbookPath,
            "--import-document-root", $ImportDocumentRoot,
            "--apply-live-writes"
        )
        foreach ($selectedPath in $selectedPaths) {
            $arguments += "--input"
            $arguments += $selectedPath
        }
    }

    if ($NoOpenReport) {
        $arguments += "--no-open-report"
    }

    $result = Invoke-ProjectJsonCommand -Arguments $arguments
    if (-not $PreviewCommand -and $result.Json) {
        Write-Section "Summary"
        Write-LauncherLine ("Run ID: {0}" -f $result.Json.run_id) "Green"
        Write-LauncherLine ("Overall decision: {0}" -f $result.Json.overall_decision) "Green"
        Write-LauncherLine ("JSON report: {0}" -f $result.Json.output_path) "Green"
        Write-LauncherLine ("HTML report: {0}" -f $result.Json.html_output_path) "Green"
        if ($null -ne $result.Json.write_execution_status) {
            Write-LauncherLine ("Workbook write status: {0}" -f $result.Json.write_execution_status) "Green"
        }
        if ($null -ne $result.Json.mail_move_status) {
            Write-LauncherLine ("Mail move status: {0}" -f $result.Json.mail_move_status) "Green"
        }
    }
    elseif ($PreviewCommand) {
        Write-LauncherLine "Preview complete. No workflow command was executed." "Yellow"
    }
}
catch {
    Write-Host ""
    Write-Host "Launcher error: $($_.Exception.Message)" -ForegroundColor Red
    Add-Content -Path $script:LauncherLogPath -Value ""
    Add-Content -Path $script:LauncherLogPath -Value "Launcher error: $($_.Exception.Message)"
    Add-Content -Path $script:LauncherLogPath -Value ($_ | Out-String)
    Write-Host "Launcher log: $script:LauncherLogPath" -ForegroundColor Yellow
    Finish-Script 1
}
finally {
    Pop-Location
}

Finish-Script 0
