$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Resolve-Path (Join-Path $ScriptDir "..\..")
$ConfigPath = Join-Path $Root "sync_config.json"
$Config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$SyncDir = [string]$Config.legacy_google_drive_sync_dir
if (-not $SyncDir) {
    $SyncDir = [string]$Config.sync_dir
}

if (-not $SyncDir) {
    throw "sync_config.json does not contain legacy_google_drive_sync_dir."
}

function Resolve-SyncDir {
    param([Parameter(Mandatory = $true)][string]$ConfiguredPath)

    $ConfiguredParent = Split-Path -Parent $ConfiguredPath
    if ($ConfiguredParent -and (Test-Path -LiteralPath $ConfiguredParent -PathType Container)) {
        return $ConfiguredPath
    }

    $Leaf = Split-Path -Leaf $ConfiguredPath
    $ShortcutCandidates = @(
        "G:\我的云端硬盘.lnk",
        "G:\My Drive.lnk",
        "G:\Google Drive.lnk"
    )
    foreach ($ShortcutPath in $ShortcutCandidates) {
        if (Test-Path -LiteralPath $ShortcutPath -PathType Leaf) {
            $Shell = New-Object -ComObject WScript.Shell
            $Shortcut = $Shell.CreateShortcut($ShortcutPath)
            $TargetPath = [string]$Shortcut.TargetPath
            $Arguments = [string]$Shortcut.Arguments
            $WorkingDirectory = [string]$Shortcut.WorkingDirectory
            Write-Host "Found Google Drive shortcut:"
            Write-Host $ShortcutPath
            Write-Host "TargetPath: $TargetPath"
            Write-Host "Arguments: $Arguments"
            Write-Host "WorkingDirectory: $WorkingDirectory"
            if ($TargetPath -and (Test-Path -LiteralPath $TargetPath -PathType Container)) {
                $Candidate = Join-Path $TargetPath $Leaf
                Write-Host "Configured sync path is not directly available:"
                Write-Host $ConfiguredPath
                Write-Host "Resolved Google Drive shortcut:"
                Write-Host "$ShortcutPath -> $TargetPath"
                Write-Host "Using:"
                Write-Host $Candidate
                return $Candidate
            }
        }
    }

    $Candidates = @(
        (Join-Path "G:\My Drive" $Leaf),
        (Join-Path "G:\我的云端硬盘" $Leaf),
        (Join-Path "$env:USERPROFILE\Google Drive\My Drive" $Leaf),
        (Join-Path "$env:USERPROFILE\Google Drive\我的云端硬盘" $Leaf),
        (Join-Path "$env:USERPROFILE\我的云端硬盘" $Leaf)
    )
    foreach ($Candidate in $Candidates) {
        $Parent = Split-Path -Parent $Candidate
        if ($Parent -and (Test-Path -LiteralPath $Parent -PathType Container)) {
            Write-Host "Configured sync path is not directly available:"
            Write-Host $ConfiguredPath
            Write-Host "Using detected Google Drive path instead:"
            Write-Host $Candidate
            return $Candidate
        }
    }

    Write-Host "Could not find the configured Google Drive folder:"
    Write-Host $ConfiguredPath
    Write-Host ""
    Write-Host "If File Explorer can open OptionsMonitorShared, paste its real path now."
    Write-Host "Tip: open that folder, click the address bar, copy the full path, then paste it here."
    $ManualPath = Read-Host "Real OptionsMonitorShared path, or press Enter to show diagnostics"
    if ($ManualPath -and (Test-Path -LiteralPath $ManualPath -PathType Container)) {
        return $ManualPath
    }
    if ($ManualPath) {
        Write-Host "The pasted path is still not a normal folder for PowerShell:"
        Write-Host $ManualPath
        Write-Host ""
    }
    Write-Host "Existing G:\ entries, if any:"
    if (Test-Path -LiteralPath "G:\") {
        Get-ChildItem -LiteralPath "G:\" -Force | ForEach-Object { Write-Host (" - " + $_.FullName) }
    } else {
        Write-Host " - G:\ is not available."
    }
    throw "Please copy the real OptionsMonitorShared path from File Explorer address bar and update sync_config.json."
}

$SyncDir = Resolve-SyncDir $SyncDir
$Config.sync_dir = $SyncDir
$Config | ConvertTo-Json | Set-Content -LiteralPath $ConfigPath -Encoding UTF8

function Ensure-Dir {
    param([Parameter(Mandatory = $true)][string]$Path)
    [System.IO.Directory]::CreateDirectory($Path) | Out-Null
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "Could not create folder: $Path"
    }
}

function Copy-DirectoryContents {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    Ensure-Dir $Destination
    if (Test-Path -LiteralPath $Source -PathType Container) {
        Get-ChildItem -LiteralPath $Source -Force | Copy-Item -Destination $Destination -Recurse -Force
    }
}

$DataDir = Join-Path $SyncDir "data"
$ReportsDir = Join-Path $SyncDir "reports"
$ConfigDir = Join-Path $SyncDir "config"

Ensure-Dir $SyncDir
Ensure-Dir $DataDir
Ensure-Dir $ReportsDir
Ensure-Dir $ConfigDir

$SharedReport = Join-Path $ReportsDir "options_anomaly_report.html"
$Overwrite = $true
if (Test-Path -LiteralPath $SharedReport) {
    Write-Host "Shared report already exists:"
    Write-Host $SharedReport
    $Answer = Read-Host "Type YES to overwrite shared data from this computer, or press Enter to keep it"
    $Overwrite = ($Answer -eq "YES")
}

if ($Overwrite) {
    Copy-DirectoryContents -Source (Join-Path $Root "data") -Destination $DataDir
    $SourceReport = Join-Path $Root "reports\options_anomaly_report.html"
    if (-not (Test-Path -LiteralPath $SourceReport -PathType Leaf)) {
        throw "Source report does not exist: $SourceReport"
    }
    Copy-Item -LiteralPath $SourceReport -Destination $SharedReport -Force
} else {
    Write-Host "Keeping existing shared data and report."
}

Copy-DirectoryContents -Source (Join-Path $Root "config") -Destination $ConfigDir

$Status = [ordered]@{
    initialized_at = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    computer = $env:COMPUTERNAME
    sync_dir = $SyncDir
}
$Status | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $SyncDir "sync_initialized.json") -Encoding UTF8

Write-Host "Google Drive sync folder initialized:"
Write-Host $SyncDir
Write-Host ""
Write-Host "You can now start the report console and open http://127.0.0.1:8765/"
Read-Host "Press Enter to close"
