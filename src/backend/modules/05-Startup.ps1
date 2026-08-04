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
#  LITERAL-NAME HELPER
# ============================================================
function ConvertTo-LiteralPropertyName {
    <# Escapes a registry VALUE NAME so the *-ItemProperty cmdlets match it
       literally.

       Remove-ItemProperty -Name accepts wildcards and has no -LiteralName
       counterpart the way -Path has -LiteralPath. Startup value names come
       from whatever an installer wrote, so a perfectly ordinary entry named
       "Acme Update [x64]" or "Sync*" would match — and DELETE — its
       siblings in the same Run key. Escaping is the only available fix. #>
    param([Parameter(Mandatory = $true)][string]$Name)
    return [System.Management.Automation.WildcardPattern]::Escape($Name)
}

# ============================================================
#  STARTUP ITEM DISCOVERY
# ============================================================
# ============================================================
#  ORIGIN LEDGER (v1.0)
#  Remembers which hive / folder a disabled item was removed from, so
#  re-enabling puts it back where it was instead of defaulting to the
#  current user. See $Script:StartupOriginRegPath in 01-Catalogs.ps1 for
#  why this lives in a sub-key rather than beside the disabled entries.
# ============================================================
function Get-StartupOriginName {
    param(
        [Parameter(Mandatory = $true)][string]$Type,
        [Parameter(Mandatory = $true)][string]$Name
    )
    return "$Type|||$Name"
}

function Save-StartupOrigin {
    <# Best-effort: losing the origin record degrades a later re-enable to
       the old per-user default, which is worse than it could be but far
       better than failing the disable the user actually asked for. #>
    param(
        [Parameter(Mandatory = $true)][string]$Type,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Origin
    )
    try {
        $Path = Resolve-UserRegPath $Script:StartupOriginRegPath
        if (-not (Test-Path $Path)) { New-Item -Path $Path -Force | Out-Null }
        Set-ItemProperty -Path $Path -Name (Get-StartupOriginName -Type $Type -Name $Name) `
            -Value $Origin -Type String -Force -ErrorAction Stop
    } catch {
        Write-Log "Could not record the origin of startup item '$Name' ($Type): $($_.Exception.Message). A later re-enable will fall back to the current user."
    }
}

function Get-StartupOrigin {
    <# The recorded origin, or $null when there is none - an item disabled by
       a pre-1.0 Pulse, or one whose record could not be written. Callers
       must treat $null as "fall back to the per-user location". #>
    param(
        [Parameter(Mandatory = $true)][string]$Type,
        [Parameter(Mandatory = $true)][string]$Name
    )
    $Value = Get-RegValue -Path $Script:StartupOriginRegPath `
        -Name (Get-StartupOriginName -Type $Type -Name $Name)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $null }
    return [string]$Value
}

function Remove-StartupOrigin {
    param(
        [Parameter(Mandatory = $true)][string]$Type,
        [Parameter(Mandatory = $true)][string]$Name
    )
    $Path = Resolve-UserRegPath $Script:StartupOriginRegPath
    if (-not (Test-Path $Path)) { return }
    Remove-ItemProperty -Path $Path `
        -Name (ConvertTo-LiteralPropertyName (Get-StartupOriginName -Type $Type -Name $Name)) `
        -ErrorAction SilentlyContinue
}

function Resolve-StartupRestoreTarget {
    <# Where an item should be put back.

       A recorded origin is only honoured if it is one of the KNOWN startup
       locations ($Script:StartupRunKeyPaths / $Script:StartupFolderPaths).
       The record lives in a user-writable hive, and this value is fed
       straight to Set-ItemProperty / Move-Item, so an allow-list is what
       keeps a tampered record from redirecting a write somewhere arbitrary.
       An unrecognised or missing origin falls back to the per-user location,
       which is exactly the pre-1.0 behaviour. #>
    param(
        [Parameter(Mandatory = $true)][string]$Type,
        [Parameter(Mandatory = $true)][string]$Name
    )
    $Known = if ($Type -eq "Registry") { $Script:StartupRunKeyPaths } else { $Script:StartupFolderPaths }
    $Fallback = $Known[0]        # per-user is always the first entry
    $Origin = Get-StartupOrigin -Type $Type -Name $Name
    if (-not $Origin) { return $Fallback }
    foreach ($Candidate in $Known) {
        if ($Origin -eq $Candidate) { return $Candidate }
    }
    Write-Log "Startup item '$Name' ($Type) recorded an unrecognised origin '$Origin' - restoring to '$Fallback' instead."
    return $Fallback
}

function Get-StartupRunKeyItems {
    $Keys = @(
        @{ Hive = "HKCU"; Path = $Script:StartupRunKeyPaths[0] },
        @{ Hive = "HKLM"; Path = $Script:StartupRunKeyPaths[1] }
    )
    $Items = @()
    foreach ($Key in $Keys) {
        if (-not (Test-Path $Key.Path)) { continue }
        $Props = Get-ItemProperty -Path $Key.Path -ErrorAction SilentlyContinue
        if (-not $Props) { continue }
        foreach ($Prop in $Props.PSObject.Properties) {
            # Skip ALL of Get-ItemProperty's synthetic PS* members. Missing
            # 'Drive' here was a real bug: the PSDrive member is a rich
            # PSDriveInfo object (with circular refs), so it both leaked a
            # bogus "PSDrive" startup item AND made ConvertTo-Json -Depth 8 in
            # Write-GuiData recurse forever — hanging the Startup Manager.
            if ($Prop.Name -match '^PS(Path|ParentPath|ChildName|Provider|Drive)$') { continue }
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
    $Folders = @($Script:StartupFolderPaths)
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
    $DisabledPath = Resolve-UserRegPath $Script:StartupDisabledRegPath
    if (Test-Path $DisabledPath) {
        $Props = Get-ItemProperty -Path $DisabledPath -ErrorAction SilentlyContinue
        if ($Props) {
            foreach ($Prop in $Props.PSObject.Properties) {
                # Skip ALL of Get-ItemProperty's synthetic PS* members. Missing
            # 'Drive' here was a real bug: the PSDrive member is a rich
            # PSDriveInfo object (with circular refs), so it both leaked a
            # bogus "PSDrive" startup item AND made ConvertTo-Json -Depth 8 in
            # Write-GuiData recurse forever — hanging the Startup Manager.
            if ($Prop.Name -match '^PS(Path|ParentPath|ChildName|Provider|Drive)$') { continue }
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
#  STARTUP OPTIMIZER — recommendation engine (v6.3, safety-tiered in v10.3)
#
#  THREE TIERS, EVALUATED IN THIS ORDER:
#    1. PROTECTED  never recommended for disabling, whatever else matches
#    2. Disable    known boot-time offenders
#    3. Keep       recognised-but-harmless publishers
#  Anything unmatched falls through to 'Review'.
#
#  WHY A THIRD TIER EXISTS. Until v10.3 there were only two lists and the
#  DISABLE list was checked first, on the reasoning that a known heavy app
#  should not be shadowed by a coincidental keep-pattern match. That
#  ordering is right for the Disable-vs-Keep question and wrong for safety:
#  it means any pattern that accidentally matches a sound driver, an input
#  helper or a security agent recommends disabling it, and the keep rule
#  written specifically to protect that component never gets consulted. The
#  cost of the two mistakes is not symmetric — over-recommending a game
#  launcher wastes nothing, while telling a user to disable their audio
#  stack or their antivirus breaks the machine and can't be spotted from
#  the row's own wording. So the protected tier goes FIRST and is absolute,
#  and the Disable-before-Keep precedence is preserved below it, unchanged.
# ============================================================

# Components that must never be recommended for disabling. Deliberately
# broader than the old keep list: it now covers the whole audio stack
# (every common vendor HDA helper, not just Realtek), input and IME,
# pointing devices, accessibility, storage/RAID drivers and endpoint
# security. These still appear in the manager and can still be toggled by
# hand — this governs what Pulse RECOMMENDS and what "Optimize Startup"
# will touch in bulk, which is the part the user is trusting.
$Script:StartupProtectedRules = @(
    @{ Pattern = 'defender|windowssecurity|securityhealth|msmpeng|msascui|smartscreen';
       Reason = "Windows Security component — disabling it weakens malware protection." }
    @{ Pattern = 'securityagent|antivirus|endpoint protection|crowdstrike|sentinelone|malwarebytes|sophos|eset|kaspersky|bitdefender|mcafee|norton|trendmicro|carbonblack';
       Reason = "Security / endpoint-protection agent — must keep running from boot to protect the machine." }
    # The audio stack, in full. A missing tray helper here does not merely
    # lose an equaliser: on many laptops it is what performs jack-detection
    # and output switching, so disabling it can leave the machine silent.
    @{ Pattern = 'realtek|rtkaud|rthdvcpl|ravcpl|rtkngui|audiodg|hdaudio|hd audio|audio.*(service|manager|control|effects)|nahimic|waves.*(maxx|audio)|maxxaudio|dolby|dtsapo|dts.*audio|sonic.*(studio|suite)|smartaudio|conexant|cxuiusvc|idt.*audio|sttray|cirrus|creative.*audio|sound.*(blaster|research)|asio';
       Reason = "Audio driver / sound-device helper — jack detection, device switching and effects depend on it." }
    @{ Pattern = 'ctfmon|tabtip|imecmnt|ime\b|inputpersonalization|textinputhost';
       Reason = "Windows text-input / IME subsystem — required for keyboard layout and language switching." }
    @{ Pattern = 'synaptics|syntpenh|elan|etdctrl|alps.*point|touchpad|precision touchpad|trackpoint';
       Reason = "Touchpad / pointing-device driver — gestures, scrolling and its settings page depend on it." }
    @{ Pattern = 'wacom|huion|xp-?pen|tablet.*(driver|service)';
       Reason = "Graphics tablet driver — pen input stops working the moment it is not running." }
    @{ Pattern = 'narrator|magnify|osk\.exe|on-?screen keyboard|accessibility|assistive';
       Reason = "Accessibility tool — for some users this is how the machine is operated at all." }
    @{ Pattern = 'iastor|rapidstorage|intel.*rapid|raid.*(monitor|service)|amd.*raid';
       Reason = "Storage / RAID controller helper — monitors the array your drives depend on." }
    @{ Pattern = 'bthserv|bluetooth.*(service|stack)|widcomm|intel.*bluetooth';
       Reason = "Bluetooth stack component — paired keyboards, mice and headsets depend on it at sign-in." }
    @{ Pattern = 'lenovo.*(power|battery)|dell.*(power|battery)|hp.*(power|battery)|power.*manager|thermal.*(manager|framework)';
       Reason = "Vendor power / thermal manager — battery life and fan behaviour are controlled here." }
)

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
    # Telemetry only. `nvcontainer` was dropped from this pattern in v10.3:
    # NVIDIA Display Container LS is what backs the control panel and the
    # driver's own settings, so recommending it for disabling was advice that
    # broke display configuration to save a few megabytes.
    @{ Pattern = 'nvidia.*telemetry|nvtelemetry';            Impact = 'Low';  Reason = "NVIDIA telemetry helper — the display driver itself does not need it at boot." }
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
foreach ($Rule in $Script:StartupProtectedRules) { $Rule.Regex = [regex]::new($Rule.Pattern, $Script:_RegexOpts) }
foreach ($Rule in $Script:StartupDisableRules)   { $Rule.Regex = [regex]::new($Rule.Pattern, $Script:_RegexOpts) }
foreach ($Rule in $Script:StartupKeepRules)      { $Rule.Regex = [regex]::new($Rule.Pattern, $Script:_RegexOpts) }

function Get-StartupRecommendation {
    <# Returns @{ Recommendation='Disable'|'Keep'|'Review'; Impact='High'|
       'Medium'|'Low'; Reason=<string>; Protected=<bool> } for one
       Get-AllStartupItems entry. Pure in-memory string/regex work - no
       registry, filesystem or network access, so this is intentionally
       cheap no matter how many startup items are being scored.

       Tier order is the safety contract - see the note above the rule
       lists. Protected wins over everything, absolutely; below it, Disable
       still beats Keep exactly as before. #>
    param($Item)
    $Hay = "$($Item.Name) $($Item.Command)"
    foreach ($Rule in $Script:StartupProtectedRules) {
        if ($Rule.Regex.IsMatch($Hay)) {
            return @{ Recommendation = 'Keep'; Impact = 'Low'; Reason = $Rule.Reason; Protected = $true }
        }
    }
    foreach ($Rule in $Script:StartupDisableRules) {
        if ($Rule.Regex.IsMatch($Hay)) {
            return @{ Recommendation = 'Disable'; Impact = $Rule.Impact; Reason = $Rule.Reason; Protected = $false }
        }
    }
    foreach ($Rule in $Script:StartupKeepRules) {
        if ($Rule.Regex.IsMatch($Hay)) {
            return @{ Recommendation = 'Keep'; Impact = 'Low'; Reason = $Rule.Reason; Protected = $false }
        }
    }
    return @{
        Recommendation = 'Review'
        Impact         = 'Medium'
        Reason         = "Not a recognized publisher — check what it is before disabling it."
        Protected      = $false
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
            # Surfaced to the GUI so a protected component can be labelled as
            # such in its row, rather than looking like an ordinary "Safe to
            # Keep" suggestion the user might reasonably overrule.
            Protected       = [bool]$Rec.Protected
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
            $DisabledPath = Resolve-UserRegPath $Script:StartupDisabledRegPath
            if (-not (Test-Path $DisabledPath)) {
                New-Item -Path $DisabledPath -Force | Out-Null
            }
            Set-ItemProperty -Path $DisabledPath -Name $Item.Name -Value $Item.Command -Force
            # Record the hive BEFORE the removal: $Item.RegPath is the only
            # place that knowledge exists, and after the delete there is no
            # way to recover whether this was an all-users (HKLM) entry.
            Save-StartupOrigin -Type "Registry" -Name $Item.Name -Origin $Item.RegPath
            Remove-ItemProperty -Path $Item.RegPath `
                -Name (ConvertTo-LiteralPropertyName $Item.Name) -ErrorAction Stop
            $Scope = if ($Item.Hive -eq "HKLM") { " (all users)" } else { "" }
            Write-Success "Disabled startup entry '$($Item.Name)'$Scope - backed up for re-enable."
        } else {
            if (-not (Test-Path $Script:StartupBackupFolder)) {
                New-Item -Path $Script:StartupBackupFolder -ItemType Directory -Force | Out-Null
            }
            # Same reasoning as the registry branch: the containing folder is
            # what distinguishes a per-user shortcut from an all-users one,
            # and moving the file destroys that evidence.
            Save-StartupOrigin -Type "Folder" -Name $Item.Name `
                -Origin (Split-Path -Path $Item.Command -Parent)
            # -LiteralPath: a shortcut called "Game [2].lnk" is an ordinary
            # filename, but -Path would read the brackets as a character class
            # and move nothing.
            Move-Item -LiteralPath $Item.Command -Destination $Script:StartupBackupFolder -Force -ErrorAction Stop
            Write-Success "Disabled startup shortcut '$($Item.Name)' (moved to backup folder)."
        }
    } catch {
        Write-ErrorX "Could not disable '$($Item.Name)': $($_.Exception.Message)"
    }
}

function Enable-StartupItem {
    <# Restores an item to the location it was DISABLED FROM, not to the
       current user. Before v1.0 both branches hard-coded the per-user
       target, so an all-users entry (HKLM Run, or a ProgramData shortcut)
       came back as a current-user-only entry: it still launched for whoever
       clicked re-enable, and silently stopped launching for every other
       account on the machine. Nothing reported that, because the operation
       itself succeeded.

       Restoring to HKLM / ProgramData needs elevation, which the dispatcher
       already requires for StartupEnableItem
       ($Script:AdminRequiredTasks, 01-Catalogs.ps1). #>
    param($Item)
    if (Test-DryRun "Re-enable startup item '$($Item.Name)' ($($Item.Type)) at its original location") { return }
    try {
        $Target = Resolve-StartupRestoreTarget -Type $Item.Type -Name $Item.Name
        if ($Item.Type -eq "Registry") {
            if (-not (Test-Path $Target)) { New-Item -Path $Target -Force | Out-Null }
            Set-ItemProperty -Path $Target -Name $Item.Name -Value $Item.Command -Force
            Remove-ItemProperty -Path (Resolve-UserRegPath $Script:StartupDisabledRegPath) `
                -Name (ConvertTo-LiteralPropertyName $Item.Name) -ErrorAction Stop
            $Scope = if ($Target -like "HKLM:*") { " for all users" } else { "" }
            Write-Success "Re-enabled startup entry '$($Item.Name)'$Scope."
        } else {
            # A recorded origin folder that has since been deleted would make
            # Move-Item fail; recreate it rather than silently relocating the
            # shortcut to a different scope than it came from.
            if (-not (Test-Path $Target)) {
                New-Item -Path $Target -ItemType Directory -Force | Out-Null
            }
            Move-Item -LiteralPath $Item.Command -Destination $Target -Force -ErrorAction Stop
            $Scope = if ($Target -eq $Script:StartupFolderPaths[1]) { " for all users" } else { "" }
            Write-Success "Re-enabled startup shortcut '$($Item.Name)'$Scope."
        }
        # Only once the restore actually succeeded - a stale origin record is
        # harmless, but dropping it before a failed move would lose the scope
        # for good on the retry.
        Remove-StartupOrigin -Type $Item.Type -Name $Item.Name
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
                Start-Process -FilePath (Get-SystemBinary "taskmgr") -ArgumentList "/7" -ErrorAction SilentlyContinue
            }
            'r' { }
            'x' { return }
        }
    } while ($true)
}
