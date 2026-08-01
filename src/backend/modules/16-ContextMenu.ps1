#Requires -Version 5.1
<#
.SYNOPSIS
    16-ContextMenu.ps1 - shell context-menu extension manager (v1.0+ F5).

.DESCRIPTION
    Right-click menus accumulate an entry from every installer that ever
    ran, and Windows ships no UI to prune them. This is that UI's backend.

    THE MECHANISM, and why it is the safe one. Windows publishes an
    official block list:

        HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\
              Shell Extensions\Blocked

    A value named after an extension's CLSID disables it shell-wide.
    Removing that value re-enables it. That is the whole operation.

    What this module therefore NEVER does is touch the extension's own
    registration. The obvious implementation - renaming
    HKCR\*\shellex\ContextMenuHandlers\<Name>, or prefixing its CLSID with
    a '-' - mutates a key the owning application also writes, so a repair
    install, an update, or the app's own repair routine can collide with
    it, and a half-renamed handler is a context menu entry that throws
    instead of one that is absent. The Blocked list is a separate,
    Microsoft-owned surface designed for exactly this, and it is additive:
    the extension's registration is left byte-for-byte intact.

    CURATED, NOT WIDE OPEN. Every handler found is REPORTED, but only
    entries matching $Script:ContextMenuAllowlist are togglable. A shell
    extension can be load-bearing - a security suite's scanner hook, a
    backup tool's overlay - and a manager that cheerfully blocks anything
    it can enumerate is a way to break a machine subtly and un-obviously.
    Unrecognised handlers are shown greyed with their owner, so the user
    can SEE what is in their menu without Pulse offering to break it.

    SNAPSHOT AND RESTORE. Every change is preceded by a full capture of
    the Blocked key to
        HKCU\Software\Pulse\ContextMenuBackup
    and Restore-PulseContextMenus puts the block list back exactly as it
    was found. Because the extensions themselves were never modified, a
    restore is complete by construction - there is no partial state to
    reconcile.

    ADMIN IS REQUIRED for the toggle and the restore: the Blocked key is
    machine-scope HKLM. The SCAN is unelevated and stays that way.
#>

$Script:ShellBlockedKey  = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Shell Extensions\Blocked'
$Script:ContextMenuBackup = 'HKCU:\Software\Pulse\ContextMenuBackup'

# Where a context-menu handler can be registered. Each is a container of
# named sub-keys whose default value is the handler's CLSID.
$Script:ContextMenuRoots = @(
    @{ Path = 'Registry::HKEY_CLASSES_ROOT\*\shellex\ContextMenuHandlers';                    Scope = "All files" }
    @{ Path = 'Registry::HKEY_CLASSES_ROOT\AllFilesystemObjects\shellex\ContextMenuHandlers'; Scope = "Files and folders" }
    @{ Path = 'Registry::HKEY_CLASSES_ROOT\Directory\shellex\ContextMenuHandlers';            Scope = "Folders" }
    @{ Path = 'Registry::HKEY_CLASSES_ROOT\Directory\Background\shellex\ContextMenuHandlers'; Scope = "Folder background" }
    @{ Path = 'Registry::HKEY_CLASSES_ROOT\Folder\shellex\ContextMenuHandlers';               Scope = "Folder objects" }
    @{ Path = 'Registry::HKEY_CLASSES_ROOT\Drive\shellex\ContextMenuHandlers';                Scope = "Drives" }
)

# ============================================================
#  THE ALLOWLIST
# ============================================================
# Handlers Pulse will offer to toggle, matched case-insensitively against
# the handler's KEY NAME or its resolved friendly name.
#
# The bar for entry: disabling it removes a menu item and nothing else.
# Deliberately ABSENT are anti-virus scan hooks, backup/versioning
# providers and anything that supplies an icon overlay rather than a menu
# item - those look like clutter and are not.
$Script:ContextMenuAllowlist = @(
    @{ Match = "7-Zip";            Label = "7-Zip";                  Owner = "7-Zip archiver" }
    @{ Match = "WinRAR";           Label = "WinRAR";                 Owner = "WinRAR archiver" }
    @{ Match = "PowerRename";      Label = "PowerRename";            Owner = "Microsoft PowerToys" }
    @{ Match = "ImageResizer";     Label = "Resize pictures";        Owner = "Microsoft PowerToys" }
    @{ Match = "Git Extensions";   Label = "Git Extensions";         Owner = "Git Extensions" }
    @{ Match = "TortoiseGit";      Label = "TortoiseGit";            Owner = "TortoiseGit" }
    @{ Match = "TortoiseSVN";      Label = "TortoiseSVN";            Owner = "TortoiseSVN" }
    @{ Match = "Notepad++";        Label = "Edit with Notepad++";    Owner = "Notepad++" }
    @{ Match = "VLC";              Label = "VLC media player";       Owner = "VideoLAN" }
    @{ Match = "Dropbox";          Label = "Dropbox";                Owner = "Dropbox" }
    @{ Match = "EPP";              Label = "Scan with Defender";     Owner = "Microsoft Defender" }
    @{ Match = "Sharing";          Label = "Give access to";         Owner = "Windows file sharing" }
    @{ Match = "Library Location"; Label = "Include in library";     Owner = "Windows libraries" }
    @{ Match = "PintoStartScreen"; Label = "Pin to Start";           Owner = "Windows Start menu" }
    @{ Match = "SendTo";           Label = "Send to";                Owner = "Windows Send to" }
    @{ Match = "OneDrive";         Label = "OneDrive";               Owner = "Microsoft OneDrive" }
)

function Resolve-ContextMenuAllowEntry {
    <# The allowlist row matching a handler, or $null when Pulse does not
       manage it. #>
    param([string]$KeyName, [string]$FriendlyName)
    foreach ($entry in $Script:ContextMenuAllowlist) {
        if (($KeyName -and $KeyName -like "*$($entry.Match)*") -or
            ($FriendlyName -and $FriendlyName -like "*$($entry.Match)*")) {
            return $entry
        }
    }
    return $null
}

function Get-ShellExtensionName {
    <# A CLSID's registered friendly name and owning module, best effort.
       Both are decoration: a handler with neither is still listed, keyed
       by its registry key name, because hiding it would be worse. #>
    param([Parameter(Mandatory)][string]$Clsid)
    $name = ""; $module = ""
    try {
        $key = "Registry::HKEY_CLASSES_ROOT\CLSID\$Clsid"
        $value = Get-ItemProperty -LiteralPath $key -Name '(default)' -ErrorAction Stop
        $name = [string]$value.'(default)'
    } catch { }
    try {
        $inproc = "Registry::HKEY_CLASSES_ROOT\CLSID\$Clsid\InprocServer32"
        $value = Get-ItemProperty -LiteralPath $inproc -Name '(default)' -ErrorAction Stop
        $module = [string]$value.'(default)'
    } catch { }
    return [PSCustomObject]@{ name = $name; module = $module }
}

function Get-BlockedShellExtensions {
    <# CLSIDs currently in Windows' block list. Unelevated-readable. #>
    $blocked = @{}
    try {
        $item = Get-Item -LiteralPath $Script:ShellBlockedKey -ErrorAction Stop
        foreach ($name in $item.GetValueNames()) {
            if ($name) { $blocked[$name.ToUpperInvariant()] = $true }
        }
    } catch { }
    return $blocked
}

function Get-PulseContextMenuItems {
    <# Every context-menu handler registered on this machine. READ-ONLY. #>
    $blocked = Get-BlockedShellExtensions
    $seen = @{}
    $items = @()

    foreach ($root in $Script:ContextMenuRoots) {
        $children = @()
        try {
            # -LiteralPath, NOT -Path. The first root is
            # HKEY_CLASSES_ROOT\*\shellex\... where the '*' is a REAL KEY
            # NAME - the handler set that applies to all file types.
            # -Path treats it as a wildcard, so PowerShell walked every
            # file-association key in HKCR looking for a match: measured at
            # 545 SECONDS for one scan, which in the GUI is a dialog that
            # never opens. -LiteralPath addresses the key that is actually
            # there and returns in milliseconds.
            $children = @(Get-ChildItem -LiteralPath $root.Path -ErrorAction Stop)
        } catch {
            continue
        }
        foreach ($child in $children) {
            $clsid = ""
            try {
                $value = Get-ItemProperty -LiteralPath $child.PSPath -Name '(default)' -ErrorAction Stop
                $clsid = ([string]$value.'(default)').Trim()
            } catch { }
            # A handler registered by name rather than CLSID cannot be
            # addressed by the Blocked list, so it is reported but never
            # togglable - the honest outcome, not a silent omission.
            $hasClsid = $clsid -match '^\{[0-9A-Fa-f\-]{36}\}$'
            $keyName = $child.PSChildName

            $dedupe = if ($hasClsid) { $clsid.ToUpperInvariant() } else { "$($root.Scope)|$keyName" }
            if ($seen.ContainsKey($dedupe)) {
                # Same handler on several roots: keep one row, widen scope.
                $existing = $items | Where-Object { $_.id -eq $dedupe } | Select-Object -First 1
                if ($existing -and $existing.scope -notlike "*$($root.Scope)*") {
                    $existing.scope = "$($existing.scope), $($root.Scope)"
                }
                continue
            }
            $seen[$dedupe] = $true

            $detail = if ($hasClsid) { Get-ShellExtensionName -Clsid $clsid }
                      else { [PSCustomObject]@{ name = ""; module = "" } }
            $allow = Resolve-ContextMenuAllowEntry -KeyName $keyName -FriendlyName $detail.name

            $items += [PSCustomObject]@{
                id       = $dedupe
                clsid    = if ($hasClsid) { $clsid } else { "" }
                keyName  = $keyName
                label    = if ($allow) { $allow.Label }
                           elseif ($detail.name) { $detail.name }
                           else { $keyName }
                owner    = if ($allow) { $allow.Owner } else { $detail.module }
                scope    = $root.Scope
                managed  = [bool]($allow -and $hasClsid)
                enabled  = -not ($hasClsid -and $blocked.ContainsKey($clsid.ToUpperInvariant()))
            }
        }
    }
    return @($items | Sort-Object -Property @{ Expression = "managed"; Descending = $true }, "label")
}

function Get-PulseContextMenuReport {
    $items = @(Get-PulseContextMenuItems)
    return [PSCustomObject]@{
        generatedAt = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
        items       = $items
        managed     = @($items | Where-Object { $_.managed }).Count
        blocked     = @($items | Where-Object { -not $_.enabled }).Count
        hasBackup   = (Test-Path -LiteralPath $Script:ContextMenuBackup)
    }
}

# ============================================================
#  SNAPSHOT / RESTORE
# ============================================================
function Save-ContextMenuSnapshot {
    <# Capture the Blocked key verbatim, ONCE.

       Once, deliberately: the snapshot is "how this machine looked before
       Pulse touched it". Refreshing it on every toggle would overwrite
       that with "before the most recent toggle", and the restore would
       then only ever undo the last change - which is not what a user who
       clicks Restore All is asking for. #>
    if (Test-Path -LiteralPath $Script:ContextMenuBackup) { return $true }
    try {
        if (-not (Test-Path -LiteralPath 'HKCU:\Software\Pulse')) {
            New-Item -Path 'HKCU:\Software\Pulse' -Force -ErrorAction Stop | Out-Null
        }
        New-Item -Path $Script:ContextMenuBackup -Force -ErrorAction Stop | Out-Null
        foreach ($clsid in (Get-BlockedShellExtensions).Keys) {
            New-ItemProperty -LiteralPath $Script:ContextMenuBackup -Name $clsid `
                -Value "" -PropertyType String -Force -ErrorAction Stop | Out-Null
        }
        # A marker so an EMPTY snapshot (nothing was blocked before Pulse)
        # is distinguishable from no snapshot at all. Without it, restoring
        # a machine that started with a clean block list would look like a
        # missing backup and be refused.
        New-ItemProperty -LiteralPath $Script:ContextMenuBackup -Name '_PulseSnapshotTaken' `
            -Value (Get-Date).ToString('o') -PropertyType String -Force -ErrorAction Stop | Out-Null
        Write-Info "Context menu block list snapshotted before the first change."
        return $true
    } catch {
        Write-ErrorX "Could not snapshot the shell block list: $($_.Exception.Message)"
        return $false
    }
}

function Set-PulseContextMenuState {
    <# Block or unblock ONE allowlisted handler by CLSID. #>
    param(
        [Parameter(Mandatory)][string]$Clsid,
        [Parameter(Mandatory)][bool]$Enabled
    )
    if ($Clsid -notmatch '^\{[0-9A-Fa-f\-]{36}\}$') {
        Write-ErrorX "'$Clsid' is not a valid shell extension CLSID."
        return $false
    }
    # Re-checked HERE, not just in the GUI: the dialog decides what to
    # OFFER, this decides what to DO, and a task that trusted its caller
    # for that would be one malformed argument away from blocking an
    # arbitrary shell extension.
    #
    # Resolved DIRECTLY rather than by scanning every handler and filtering
    # - the guard needs to answer "is THIS clsid allowlisted", and running
    # a full enumeration to answer it made each toggle pay for a whole
    # scan.
    $detail = Get-ShellExtensionName -Clsid $Clsid
    $allow = Resolve-ContextMenuAllowEntry -KeyName $detail.name -FriendlyName $detail.name
    if (-not $allow) {
        Write-ErrorX "$Clsid is not a context-menu handler Pulse manages."
        return $false
    }
    $label = $allow.Label

    if ($Script:DryRun) {
        $verb = if ($Enabled) { "Re-enable" } else { "Block" }
        Write-Host "   [WHATIF] $verb shell extension $Clsid ($label)"
        return $true
    }
    if (-not (Save-ContextMenuSnapshot)) { return $false }

    try {
        if (-not (Test-Path -LiteralPath $Script:ShellBlockedKey)) {
            New-Item -Path $Script:ShellBlockedKey -Force -ErrorAction Stop | Out-Null
        }
        if ($Enabled) {
            Remove-ItemProperty -LiteralPath $Script:ShellBlockedKey -Name $Clsid `
                -Force -ErrorAction SilentlyContinue
            Write-Success "$label restored to the context menu."
        } else {
            New-ItemProperty -LiteralPath $Script:ShellBlockedKey -Name $Clsid `
                -Value "" -PropertyType String -Force -ErrorAction Stop | Out-Null
            Write-Success "$label removed from the context menu."
        }
        return $true
    } catch {
        Write-ErrorX "Could not update the shell block list: $($_.Exception.Message)"
        return $false
    }
}

function Restore-PulseContextMenus {
    <# Put the block list back exactly as the snapshot found it.

       Complete by construction: the extensions' own registrations were
       never modified, so the block list IS the entire footprint. #>
    if (-not (Test-Path -LiteralPath $Script:ContextMenuBackup)) {
        Write-ErrorX "No context-menu snapshot exists - nothing to restore."
        return $false
    }
    if ($Script:DryRun) {
        Write-Host "   [WHATIF] Restore the shell block list from the Pulse snapshot"
        return $true
    }
    try {
        $snapshot = @{}
        $item = Get-Item -LiteralPath $Script:ContextMenuBackup -ErrorAction Stop
        foreach ($name in $item.GetValueNames()) {
            if ($name -and $name -ne '_PulseSnapshotTaken') {
                $snapshot[$name.ToUpperInvariant()] = $true
            }
        }
        if (-not (Test-Path -LiteralPath $Script:ShellBlockedKey)) {
            New-Item -Path $Script:ShellBlockedKey -Force -ErrorAction Stop | Out-Null
        }
        $current = Get-BlockedShellExtensions

        # Remove blocks Pulse added...
        foreach ($clsid in $current.Keys) {
            if (-not $snapshot.ContainsKey($clsid)) {
                Remove-ItemProperty -LiteralPath $Script:ShellBlockedKey -Name $clsid `
                    -Force -ErrorAction SilentlyContinue
            }
        }
        # ...and put back any the machine had before Pulse and that have
        # since been removed by something else.
        foreach ($clsid in $snapshot.Keys) {
            if (-not $current.ContainsKey($clsid)) {
                New-ItemProperty -LiteralPath $Script:ShellBlockedKey -Name $clsid `
                    -Value "" -PropertyType String -Force -ErrorAction Stop | Out-Null
            }
        }
        Write-Success "Context menu restored to its pre-Pulse state."
        return $true
    } catch {
        Write-ErrorX "Could not restore the shell block list: $($_.Exception.Message)"
        return $false
    }
}
