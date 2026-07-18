#Requires -Version 5.1
<#
.SYNOPSIS
    05-Startup.ps1 - startup program discovery, disable/re-enable and the
    interactive Startup Program Manager.

.DESCRIPTION
    Sources audited: HKCU/HKLM Run keys and the per-user/all-users Startup
    folders. Disabling is always reversible: registry entries are copied to
    HKCU:\Software\Pulse\DisabledStartup before removal, and
    shortcuts are MOVED to Desktop\Pulse_StartupBackup, never deleted.
    Locations are defined in 01-Catalogs.ps1.
#>

# ============================================================
#  STARTUP ITEM DISCOVERY
# ============================================================
function Get-StartupRunKeyItems {
    $Keys = @(
        @{ Hive = "HKCU"; Path = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" },
        @{ Hive = "HKLM"; Path = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" }
    )
    $Items = @()
    foreach ($Key in $Keys) {
        if (-not (Test-Path $Key.Path)) { continue }
        $Props = Get-ItemProperty -Path $Key.Path -ErrorAction SilentlyContinue
        if (-not $Props) { continue }
        foreach ($Prop in $Props.PSObject.Properties) {
            if ($Prop.Name -match '^PS(Path|ParentPath|ChildName|Provider)$') { continue }
            $Items += [PSCustomObject]@{
                Type    = "Registry"
                Hive    = $Key.Hive
                RegPath = $Key.Path
                Name    = $Prop.Name
                Command = $Prop.Value
                Enabled = $true
            }
        }
    }
    return $Items
}

function Get-StartupFolderItems {
    $Folders = @(
        "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup",
        "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\Startup"
    )
    $Items = @()
    foreach ($Folder in $Folders) {
        if (-not (Test-Path $Folder)) { continue }
        Get-ChildItem -Path $Folder -File -ErrorAction SilentlyContinue | ForEach-Object {
            $Items += [PSCustomObject]@{
                Type    = "Folder"
                Hive    = ""
                RegPath = $Folder
                Name    = $_.Name
                Command = $_.FullName
                Enabled = $true
            }
        }
    }
    return $Items
}

function Get-DisabledStartupItems {
    $Items = @()
    if (Test-Path $Script:StartupDisabledRegPath) {
        $Props = Get-ItemProperty -Path $Script:StartupDisabledRegPath -ErrorAction SilentlyContinue
        if ($Props) {
            foreach ($Prop in $Props.PSObject.Properties) {
                if ($Prop.Name -match '^PS(Path|ParentPath|ChildName|Provider)$') { continue }
                $Items += [PSCustomObject]@{
                    Type    = "Registry"
                    Hive    = "HKCU"
                    RegPath = $Script:StartupDisabledRegPath
                    Name    = $Prop.Name
                    Command = $Prop.Value
                    Enabled = $false
                }
            }
        }
    }
    if (Test-Path $Script:StartupBackupFolder) {
        Get-ChildItem -Path $Script:StartupBackupFolder -File -ErrorAction SilentlyContinue | ForEach-Object {
            $Items += [PSCustomObject]@{
                Type    = "Folder"
                Hive    = ""
                RegPath = $Script:StartupBackupFolder
                Name    = $_.Name
                Command = $_.FullName
                Enabled = $false
            }
        }
    }
    return $Items
}

function Get-AllStartupItems {
    return @(Get-StartupRunKeyItems) + @(Get-StartupFolderItems) + @(Get-DisabledStartupItems)
}

function Show-StartupItemsList {
    param([array]$Items)
    if ($Items.Count -eq 0) {
        Write-Info "No startup items found."
        return
    }
    for ($i = 0; $i -lt $Items.Count; $i++) {
        $it = $Items[$i]
        $StatusTag = if ($it.Enabled) { "ENABLED " } else { "DISABLED" }
        $Color = if ($it.Enabled) { "Green" } else { "DarkGray" }
        Write-Host ("   [{0,2}] [{1}] {2}  ({3})" -f ($i + 1), $StatusTag, $it.Name, $it.Type) -ForegroundColor $Color
    }
}

# ============================================================
#  DISABLE / RE-ENABLE (reversible, dry-run aware)
# ============================================================
function Disable-StartupItem {
    param($Item)
    if (Test-DryRun "Disable startup item '$($Item.Name)' ($($Item.Type)) - backed up for re-enable") { return }
    try {
        if ($Item.Type -eq "Registry") {
            if (-not (Test-Path $Script:StartupDisabledRegPath)) {
                New-Item -Path $Script:StartupDisabledRegPath -Force | Out-Null
            }
            Set-ItemProperty -Path $Script:StartupDisabledRegPath -Name $Item.Name -Value $Item.Command -Force
            Remove-ItemProperty -Path $Item.RegPath -Name $Item.Name -ErrorAction Stop
            Write-Success "Disabled startup entry '$($Item.Name)' (backed up for re-enable)."
        } else {
            if (-not (Test-Path $Script:StartupBackupFolder)) {
                New-Item -Path $Script:StartupBackupFolder -ItemType Directory -Force | Out-Null
            }
            Move-Item -Path $Item.Command -Destination $Script:StartupBackupFolder -Force -ErrorAction Stop
            Write-Success "Disabled startup shortcut '$($Item.Name)' (moved to backup folder)."
        }
    } catch {
        Write-ErrorX "Could not disable '$($Item.Name)': $($_.Exception.Message)"
    }
}

function Enable-StartupItem {
    param($Item)
    if (Test-DryRun "Re-enable startup item '$($Item.Name)' ($($Item.Type))") { return }
    try {
        if ($Item.Type -eq "Registry") {
            if (-not (Test-Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run")) {
                New-Item -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Force | Out-Null
            }
            Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name $Item.Name -Value $Item.Command -Force
            Remove-ItemProperty -Path $Script:StartupDisabledRegPath -Name $Item.Name -ErrorAction Stop
            Write-Success "Re-enabled startup entry '$($Item.Name)'."
        } else {
            $Dest = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup"
            Move-Item -Path $Item.Command -Destination $Dest -Force -ErrorAction Stop
            Write-Success "Re-enabled startup shortcut '$($Item.Name)'."
        }
    } catch {
        Write-ErrorX "Could not re-enable '$($Item.Name)': $($_.Exception.Message)"
    }
}

# ============================================================
#  INTERACTIVE STARTUP PROGRAM MANAGER (console mode only)
# ============================================================
function Show-StartupProgramManager {
    do {
        Write-Banner "STARTUP PROGRAM MANAGER"
        $AllItems      = Get-AllStartupItems
        $EnabledCount  = ($AllItems | Where-Object { $_.Enabled }).Count
        $DisabledCount = ($AllItems | Where-Object { -not $_.Enabled }).Count
        Write-Info "$EnabledCount enabled / $DisabledCount disabled startup item(s) detected."
        Write-Host ""
        Show-StartupItemsList -Items $AllItems
        Write-Divider
        Write-Host "   [D]  Disable an item" -ForegroundColor White
        Write-Host "   [E]  Re-enable a disabled item" -ForegroundColor White
        Write-Host "   [T]  Open Task Manager (Startup tab)" -ForegroundColor White
        Write-Host "   [R]  Refresh list" -ForegroundColor DarkGray
        Write-Host "   [X]  Back to Main Menu" -ForegroundColor DarkGray
        Write-Divider
        $Choice = Read-Choice -Prompt "   Select an action" -Valid @('d','e','t','r','x')

        switch ($Choice) {
            'd' {
                if (($AllItems | Where-Object { $_.Enabled }).Count -eq 0) {
                    Write-Warn "No enabled items to disable."; Start-Sleep -Seconds 1; continue
                }
                $Idx = Read-NumericChoice -Prompt "   Enter item number to disable (list above)" -Max $AllItems.Count
                if ($null -ne $Idx) {
                    $Target = $AllItems[$Idx - 1]
                    if ($Target.Enabled) {
                        if (Ask-User "Disable '$($Target.Name)'" "Prevents this program from launching at sign-in. A backup is kept so it can be re-enabled.") {
                            Disable-StartupItem -Item $Target
                        }
                    } else {
                        Write-AlreadyOK "'$($Target.Name)' is already disabled."
                    }
                } else {
                    Write-Warn "Invalid item number."
                }
                Start-Sleep -Seconds 1
            }
            'e' {
                if (($AllItems | Where-Object { -not $_.Enabled }).Count -eq 0) {
                    Write-Warn "No disabled items to re-enable."; Start-Sleep -Seconds 1; continue
                }
                $Idx = Read-NumericChoice -Prompt "   Enter item number to re-enable (list above)" -Max $AllItems.Count
                if ($null -ne $Idx) {
                    $Target = $AllItems[$Idx - 1]
                    if (-not $Target.Enabled) {
                        Enable-StartupItem -Item $Target
                    } else {
                        Write-AlreadyOK "'$($Target.Name)' is already enabled."
                    }
                } else {
                    Write-Warn "Invalid item number."
                }
                Start-Sleep -Seconds 1
            }
            't' {
                Write-Info "Opening Task Manager..."
                Start-Process "taskmgr.exe" -ArgumentList "/7" -ErrorAction SilentlyContinue
            }
            'r' { }
            'x' { return }
        }
    } while ($true)
}
