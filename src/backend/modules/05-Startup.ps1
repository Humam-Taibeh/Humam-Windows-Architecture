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

# ============================================================
#  STARTUP OPTIMIZER — recommendation engine (v6.3)
#  Pattern-matched against "Name Command" (lowercased). Order matters:
#  the disable list is checked first, so a known heavy app never gets
#  shadowed by a coincidental keep-pattern match.
# ============================================================
$Script:StartupDisableRules = @(
    @{ Pattern = 'onedrive';                              Impact = 'Medium'; Reason = "Cloud sync — keeps syncing in the background; launch it manually or sign in to files.com when you actually need it." }
    @{ Pattern = 'dropbox';                                Impact = 'Medium'; Reason = "Cloud sync client — adds boot time for a service you can start on demand." }
    @{ Pattern = 'steam';                                  Impact = 'High';   Reason = "Game launcher with background update checks — a common multi-second boot delay." }
    @{ Pattern = 'epicgameslauncher|epic games';           Impact = 'High';   Reason = "Game launcher — heavy background process not needed until you actually play." }
    @{ Pattern = 'battle\.net|blizzard';                   Impact = 'High';   Reason = "Game launcher with an always-on updater service." }
    @{ Pattern = 'origin|ea desktop|eadesktop';            Impact = 'High';   Reason = "Game launcher — safe to start manually instead of at every boot." }
    @{ Pattern = 'riot client|riotclient';                 Impact = 'Medium'; Reason = "Game launcher background updater." }
    @{ Pattern = 'ubisoft connect|uplay';                  Impact = 'Medium'; Reason = "Game launcher background updater." }
    @{ Pattern = 'discord';                                Impact = 'Medium'; Reason = "Chat client — convenient always-on, but it's pure boot-time overhead if you open it manually anyway." }
    @{ Pattern = 'spotify';                                Impact = 'Medium'; Reason = "Music client — no reason to launch before you're ready to listen." }
    @{ Pattern = 'skype';                                  Impact = 'Medium'; Reason = "Chat client that rarely needs to be running before sign-in finishes." }
    @{ Pattern = 'teams|squirrel\.exe.*teams';             Impact = 'High';   Reason = "Electron-based chat app — one of the heaviest common boot-time offenders." }
    @{ Pattern = 'slack';                                  Impact = 'Medium'; Reason = "Electron-based chat app — noticeable boot-time cost for a background presence." }
    @{ Pattern = 'zoom';                                   Impact = 'Low';    Reason = "Meeting client — only needed right before a call." }
    @{ Pattern = 'adobe.*(updater|arm\.exe|armsvc)|adobearm'; Impact = 'Low'; Reason = "Adobe's background updater — checks for updates you can trigger manually instead." }
    @{ Pattern = 'itunes|applemobiledevicehelper|ituneshelper'; Impact = 'Medium'; Reason = "Apple device helper — only useful while an iPhone/iPad is actually connected." }
    @{ Pattern = 'quicktime';                               Impact = 'Low';    Reason = "Legacy media helper rarely needed by modern apps." }
    @{ Pattern = 'googleupdate|googlechromeautolaunch|gupdate'; Impact = 'Low'; Reason = "Chrome's background updater — Chrome updates itself fine when it launches." }
    @{ Pattern = 'msedgeupdate|microsoftedgeupdate';        Impact = 'Low';    Reason = "Edge's background updater — Edge updates itself fine when it launches." }
    @{ Pattern = 'cortana';                                 Impact = 'Low';    Reason = "Legacy Cortana shell integration — safe to disable on most modern setups." }
    @{ Pattern = 'yourphone|phonelink';                     Impact = 'Low';    Reason = "Phone Link — only useful if you actively use phone/PC linking." }
    @{ Pattern = 'creativecloud|cc[_ ]?library|coreSync';   Impact = 'High';   Reason = "Adobe Creative Cloud desktop — one of the heaviest known startup offenders." }
    @{ Pattern = 'javaupdater|jusched';                      Impact = 'Low';    Reason = "Java's background updater — safe to check manually instead." }
    @{ Pattern = 'nvidia.*(container|telemetry)|nvcontainer'; Impact = 'Low';  Reason = "NVIDIA telemetry/container helper — the display driver itself does not need it at boot." }
)

$Script:StartupKeepRules = @(
    @{ Pattern = 'defender|windowssecurity|securityhealth|msmpeng'; Reason = "Windows Security — disabling weakens malware protection." }
    @{ Pattern = 'realtek|rtkaud|audio.*service|nahimic';           Reason = "Audio driver tray helper — needed for sound device switching/effects to work correctly." }
    @{ Pattern = 'synaptics|elan|touchpad|precision touchpad';       Reason = "Touchpad/precision-input driver — gestures and settings depend on it." }
    @{ Pattern = 'nvidia.*(tray|settings)|nvtray|nvidia share';      Reason = "GPU control panel tray — lightweight and needed for display/overlay settings." }
    @{ Pattern = 'radeon software|amd.*(tray|external)';             Reason = "GPU control panel tray — lightweight and needed for display/overlay settings." }
    @{ Pattern = 'ctfmon';                                            Reason = "Windows input/IME subsystem — required for text input switching." }
    @{ Pattern = 'securityagent|antivirus|endpoint protection|crowdstrike|sentinelone|malwarebytes'; Reason = "Security/endpoint-protection agent — should stay running from boot." }
    @{ Pattern = 'wacom|huion';                                       Reason = "Graphics tablet driver — needed immediately for pen input to work." }
)

# Pre-compiled once at module load, not re-compiled on every -match call
# against every item - cheap either way at typical startup-list sizes, but
# this is the correct pattern for "fast lookups, never heavy work per item"
# and keeps Get-StartupRecommendation's per-item cost to pure in-memory
# regex evaluation with zero I/O.
$Script:_RegexOpts = [System.Text.RegularExpressions.RegexOptions]::IgnoreCase -bor `
    [System.Text.RegularExpressions.RegexOptions]::Compiled
foreach ($Rule in $Script:StartupDisableRules) { $Rule.Regex = [regex]::new($Rule.Pattern, $Script:_RegexOpts) }
foreach ($Rule in $Script:StartupKeepRules)    { $Rule.Regex = [regex]::new($Rule.Pattern, $Script:_RegexOpts) }

function Get-StartupRecommendation {
    <# Returns @{ Recommendation='Disable'|'Keep'|'Review'; Impact='High'|
       'Medium'|'Low'; Reason=<string> } for one Get-AllStartupItems entry.
       Pure in-memory string/regex work - no registry, filesystem or
       network access, so this is intentionally cheap no matter how many
       startup items are being scored. #>
    param($Item)
    $Hay = "$($Item.Name) $($Item.Command)"
    foreach ($Rule in $Script:StartupDisableRules) {
        if ($Rule.Regex.IsMatch($Hay)) {
            return @{ Recommendation = 'Disable'; Impact = $Rule.Impact; Reason = $Rule.Reason }
        }
    }
    foreach ($Rule in $Script:StartupKeepRules) {
        if ($Rule.Regex.IsMatch($Hay)) {
            return @{ Recommendation = 'Keep'; Impact = 'Low'; Reason = $Rule.Reason }
        }
    }
    return @{
        Recommendation = 'Review'
        Impact         = 'Medium'
        Reason         = "Not a recognized publisher — check what it is before disabling it."
    }
}

$Script:StartupImpactRank = @{ High = 0; Medium = 1; Low = 2 }

function Get-StartupReportData {
    <# The Startup Manager's full dataset: every discovered item plus its
       recommendation, sorted enabled-first then by impact severity - the
       items most worth acting on land at the top of the GUI's list. Each
       item carries a stable `Id` ("Type|||RegPath|||Name") that
       Resolve-StartupItemByEncodedId uses to re-locate the exact same item
       on a later toggle call (a fresh process, with no memory of this
       scan). #>
    $Result = @()
    foreach ($It in @(Get-AllStartupItems)) {
        $Rec = Get-StartupRecommendation -Item $It
        $Result += [PSCustomObject]@{
            Id              = "$($It.Type)|||$($It.RegPath)|||$($It.Name)"
            Name            = $It.Name
            Type            = $It.Type
            Command         = $It.Command
            Enabled         = [bool]$It.Enabled
            Recommendation  = $Rec.Recommendation
            Impact          = $Rec.Impact
            Reason          = $Rec.Reason
        }
    }
    return $Result | Sort-Object `
        @{ Expression = { if ($_.Enabled) { 0 } else { 1 } } }, `
        @{ Expression = { $Script:StartupImpactRank[$_.Impact] } }, `
        Name
}

function Resolve-StartupItemByEncodedId {
    <# Reverses Get-StartupReportData's Id back into the live item object
       Disable-StartupItem/Enable-StartupItem expect, by re-scanning and
       matching on (Type, RegPath, Name) - the same identity triple, never
       a stale snapshot from a previous process. #>
    param([string]$EncodedId)
    $Parts = $EncodedId -split '\|\|\|', 3
    if ($Parts.Count -lt 3) { return $null }
    $Type, $RegPath, $Name = $Parts
    return (Get-AllStartupItems | Where-Object {
        $_.Type -eq $Type -and $_.RegPath -eq $RegPath -and $_.Name -eq $Name
    } | Select-Object -First 1)
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
