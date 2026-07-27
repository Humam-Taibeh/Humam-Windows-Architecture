#Requires -Version 5.1
<#
.SYNOPSIS
    04-SoftwareEngine.ps1 - the winget/Chocolatey deployment engine.

.DESCRIPTION
    Smart-Deploy is the single entry point for installing/upgrading any app.
    It is fed exclusively by the catalogs in 01-Catalogs.ps1 (data-driven:
    no per-app functions), and it honors three global modes:
      - $Script:NonInteractive : GUI task mode - never prompts, never pops
        browsers or the Microsoft Store mid-silent-run.
      - $Script:DryRun (-WhatIf): reports what WOULD be installed/upgraded
        and returns Status='Success' without touching the system.
      - Bulk/BulkMethod: category-wide auto or manual handling.

    Also: version probing, store-app detection, hardware matching (GPU /
    motherboard vendor apps) and the interactive category processor.
#>

# ============================================================
#  STORE APP DETECTION
# ============================================================
function Is-StoreApp {
    param([string]$AppId)
    return $AppId -match '^\w{12}$'
}

# ============================================================
#  INSTALLED / LATEST VERSION DETECTION
# ============================================================
# Bulk-batch cache (Id/Name -> {Installed, Available}) populated once per
# batch by Initialize-WingetBatchCache (see Process-AppCategory below) from
# a single `winget list` call. $null outside an active batch, so single
# (non-bulk) probes keep querying winget live as before - only Process-
# AppCategory's bulk loops set/clear it, so staleness can never leak into
# an unrelated individual install later in the same session.
$Script:WingetBatchCache = $null

function Initialize-WingetBatchCache {
    <#
    .SYNOPSIS
        Builds the one-shot Id/Name -> {Installed, Available} lookup used
        by Get-InstalledVersion/Get-LatestVersion during a bulk deployment,
        from a single `winget list` call instead of one winget process per
        app. `winget list`'s column layout (Name/Id/Version/Available/
        Source) is identical to `winget upgrade`'s, so the existing
        ConvertFrom-WingetUpgradeTable parser is reused as-is.
    #>
    $Script:WingetBatchCache = @{ ById = @{}; ByName = @{} }
    if (-not $global:WingetAvailable) { return }
    try {
        $Raw = & winget list --accept-source-agreements --disable-interactivity 2>$null
        foreach ($Item in (ConvertFrom-WingetUpgradeTable -Raw $Raw)) {
            $Entry = @{ Installed = $Item.CurrentVersion; Available = $Item.AvailableVersion }
            $Script:WingetBatchCache.ById[$Item.Id]     = $Entry
            $Script:WingetBatchCache.ByName[$Item.Name] = $Entry
        }
    } catch {
        Write-Log "Winget batch cache build failed: $($_.Exception.Message)"
    }
}

function Get-InstalledVersion {
    param([string]$AppId, [string]$AppName)

    if (Is-StoreApp $AppId) {
        try {
            $pkg = Get-AppxPackage -Name $AppId -ErrorAction SilentlyContinue
            if ($pkg) { return $pkg.Version }
        } catch {}
        return $null
    }

    if (-not $global:WingetAvailable) { return $null }  # no winget -> no probe

    if ($Script:WingetBatchCache) {
        # Active bulk batch: the one-shot `winget list` cache is
        # authoritative - querying it instead of spawning a fresh winget
        # process per app is the entire point of the batch cache.
        if ($Script:WingetBatchCache.ById.ContainsKey($AppId)) { return $Script:WingetBatchCache.ById[$AppId].Installed }
        if ($Script:WingetBatchCache.ByName.ContainsKey($AppName)) { return $Script:WingetBatchCache.ByName[$AppName].Installed }
        return $null
    }

    $Lines = & winget list --id $AppId --exact --accept-source-agreements --disable-interactivity 2>$null
    if (-not $Lines) {
        $Lines = & winget list --query $AppName --exact --accept-source-agreements --disable-interactivity 2>$null
    }
    if (-not $Lines) { return $null }

    foreach ($Line in $Lines) {
        $Trimmed = $Line.Trim()
        if ([string]::IsNullOrWhiteSpace($Trimmed)) { continue }
        # NOT a `\s{2,}` column split: winget only right-pads columns to
        # align them for a real interactive console. The instant Pulse
        # captures its output (every call site here does), that alignment
        # can collapse to single spaces, so a 2+-space split silently
        # merges the Id/Version/Source columns into the Name column and
        # this always returned null - the "instant skip" fast path quietly
        # never firing, every deploy falling through to a live winget
        # upgrade call it didn't need to make. IDs and versions never
        # contain spaces, so instead: split on ANY whitespace and read the
        # token AFTER an exact AppId match as the version.
        $Tokens = [regex]::Split($Trimmed, '\s+')
        # AppId match takes strict priority and is checked in its own pass:
        # winget package IDs (e.g. "Git.Git") never collide with a Name
        # column value, whereas Pulse's own catalog display name
        # occasionally could (e.g. "7-Zip") if it's a single word AND
        # happens to precede the real Id token - checking AppId first,
        # fully, before ever falling back to AppName avoids that.
        for ($i = 0; $i -lt $Tokens.Count - 1; $i++) {
            if ($Tokens[$i] -eq $AppId) { return $Tokens[$i + 1] }
        }
        for ($i = 0; $i -lt $Tokens.Count - 1; $i++) {
            if ($Tokens[$i] -eq $AppName) { return $Tokens[$i + 1] }
        }
    }
    return $null
}

function Get-LatestVersion {
    param([string]$AppId)
    if (Is-StoreApp $AppId) {
        # "Store" (not "Unknown") is the deliberate sentinel meaning "can't
        # probe a real version, treat any installed copy as current" -
        # Smart-Deploy's store-app branch reads it exactly that way.
        # winget's msstore source, when reachable, gives a real version to
        # compare against Get-AppxPackage's installed version instead.
        if (-not $global:WingetAvailable) { return "Store" }
        $Lines = & winget show --id $AppId --exact --source msstore --accept-source-agreements --disable-interactivity 2>$null
        if (-not $Lines) { return "Store" }
        foreach ($Line in $Lines) {
            if ($Line -match '^\s*Version:\s*(\S+)') { return $Matches[1] }
        }
        return "Store"
    }
    if (-not $global:WingetAvailable) { return "Unknown" }  # no winget -> no probe

    if ($Script:WingetBatchCache) {
        if ($Script:WingetBatchCache.ById.ContainsKey($AppId)) {
            $Entry = $Script:WingetBatchCache.ById[$AppId]
            if (-not [string]::IsNullOrWhiteSpace($Entry.Available)) { return $Entry.Available }
            return $Entry.Installed   # blank Available in `winget list` -> no pending upgrade, already latest
        }
        return "Unknown"   # not installed - the batch cache has no manifest data to probe for uninstalled apps
    }

    $Lines = & winget show --id $AppId --exact --accept-source-agreements --disable-interactivity 2>$null
    if (-not $Lines) { return "Unknown" }
    foreach ($Line in $Lines) {
        if ($Line -match '^\s*Version:\s*(\S+)') { return $Matches[1] }
    }
    return "Unknown"
}

# ============================================================
#  UPDATE CENTER — winget upgrade scan (v6.3)
# ============================================================
function ConvertFrom-WingetUpgradeTable {
    <#
    .SYNOPSIS
        Parses `winget upgrade`'s aligned text table into an array of
        PSCustomObject { Id, Name, CurrentVersion, AvailableVersion }.
        Source-agnostic - used for both the default multi-source scan and
        the msstore-scoped one below, since the column layout is identical.

    .DESCRIPTION
        `winget upgrade` has no --output json in the stable CLI, so this
        parses its aligned text table the same way every serious community
        tool does: read the column START OFFSETS from the header row itself
        (Name / Id / Version / Available[/ Source]), then slice each data
        row by those offsets - never by splitting on whitespace, which
        breaks the instant an app name contains a space (most of them do).
        Malformed/unrecognized rows are skipped individually rather than
        aborting the whole scan - a partial result beats a hard failure.
    #>
    param([string[]]$Raw)

    if (-not $Raw) { return @() }
    $Lines = @($Raw | Where-Object { $_ -and $_.Trim() -ne '' })

    $HeaderIdx = -1
    for ($i = 0; $i -lt $Lines.Count; $i++) {
        if ($Lines[$i] -match '^Name\s+Id\s+Version\s+Available') { $HeaderIdx = $i; break }
    }
    if ($HeaderIdx -eq -1) { return @() }   # "No installed package found..." / "No applicable update found"

    $Header = $Lines[$HeaderIdx]
    $NameStart      = $Header.IndexOf("Name")
    $IdStart        = $Header.IndexOf("Id")
    $VersionStart   = $Header.IndexOf("Version")
    $AvailableStart = $Header.IndexOf("Available")
    $SourceStart    = $Header.IndexOf("Source")   # may be -1 (msstore-only listings omit it)

    $Items = @()
    for ($i = $HeaderIdx + 2; $i -lt $Lines.Count; $i++) {   # +2 skips header + "----" separator
        $Line = $Lines[$i]
        if ($Line -match '^\d+\s+upgrades?\s+available' -or $Line -match '^-+$') { continue }
        if ($Line -match '^\d+\s+package\(s\)') { continue }
        if ($Line.Length -le $IdStart) { continue }
        try {
            $AvailEnd = if ($SourceStart -gt $AvailableStart) { $SourceStart } else { $Line.Length }
            $Name = $Line.Substring($NameStart, [Math]::Min($IdStart, $Line.Length) - $NameStart).Trim()
            $Id   = $Line.Substring($IdStart, [Math]::Min($VersionStart, $Line.Length) - $IdStart).Trim()
            $Ver  = $Line.Substring($VersionStart, [Math]::Min($AvailableStart, $Line.Length) - $VersionStart).Trim()
            $Avail = $Line.Substring($AvailableStart, [Math]::Min($AvailEnd, $Line.Length) - $AvailableStart).Trim()
            if ([string]::IsNullOrWhiteSpace($Id) -or [string]::IsNullOrWhiteSpace($Name)) { continue }
            # "have an upgrade available but require explicit targeting" -
            # winget still lists them with a version of "Unknown"; keep them
            # (--include-unknown asked for exactly this) but the frontend
            # audit reads better with the raw values, so pass through as-is.
            $Items += [PSCustomObject]@{
                Id              = $Id
                Name            = $Name
                CurrentVersion  = $Ver
                AvailableVersion = $Avail
            }
        } catch {
            continue   # one unparsable row never aborts the whole scan
        }
    }
    return $Items
}

function Get-WingetUpgradeList {
    <#
    .SYNOPSIS
        Returns every app winget reports as upgradable - Win32 packages AND
        Microsoft Store apps - as an array of PSCustomObject { Id, Name,
        CurrentVersion, AvailableVersion }, so Update Center's "Update All"
        can process both kinds in one unified batch.

    .DESCRIPTION
        The default (source-less) `winget upgrade` call is expected to
        cover every configured source including msstore, but in practice
        Microsoft Store packages carry their own per-source terms-of-
        transaction agreement - a source that hasn't separately accepted it
        gets silently dropped from the unscoped listing even with
        --accept-source-agreements. Explicitly scanning `--source msstore`
        (which DOES accept that source's agreement when scoped to it) is
        what reliably surfaces Store app updates, so both scans always run
        and are merged, de-duplicated by Id (the default scan winning any
        overlap since it's the richer/authoritative pass).
    #>
    if (-not $global:WingetAvailable) { return @() }

    $WingetRaw = & winget upgrade --include-unknown --accept-source-agreements --disable-interactivity 2>$null
    $WingetItems = @(ConvertFrom-WingetUpgradeTable -Raw $WingetRaw)

    $StoreItems = @()
    try {
        $StoreRaw = & winget upgrade --include-unknown --source msstore --accept-source-agreements --disable-interactivity 2>$null
        $StoreItems = @(ConvertFrom-WingetUpgradeTable -Raw $StoreRaw)
    } catch {
        Write-Log "msstore upgrade scan failed: $($_.Exception.Message)"
    }

    $SeenIds = @{}
    $Items = @()
    foreach ($Item in ($WingetItems + $StoreItems)) {
        if ($SeenIds.ContainsKey($Item.Id)) { continue }
        $SeenIds[$Item.Id] = $true
        $Items += $Item
    }
    return $Items
}

# ============================================================
#  WINGET / CHOCOLATEY EXECUTION
# ============================================================
function Stop-LockingProcesses {
    param($AppId)
    if (-not $Script:LockProcessMap.ContainsKey($AppId)) { return }

    # Kill every matching lock process first, then sleep once at the end -
    # sleeping 800ms after EACH kill serialized the wait time across every
    # matched process name (N processes = N x 800ms) for no benefit, since
    # nothing reads process state between one Stop-Process call and the next.
    $Killed = $false
    foreach ($ProcName in $Script:LockProcessMap[$AppId]) {
        $Proc = Get-Process -Name $ProcName -ErrorAction SilentlyContinue
        if ($Proc) {
            Invoke-Mutation -Description "Terminate background process '$ProcName' (locks the $AppId installer)" -Action {
                Write-Warn "Terminating background process '$ProcName'..."
                Stop-Process -Name $ProcName -Force -ErrorAction SilentlyContinue
            } | Out-Null
            # Dry-run: Invoke-Mutation logs [WHATIF] and never actually kills
            # anything, so the trailing sleep (which exists to give a REAL
            # kill time to release its file lock) must not fire either.
            if (-not $Script:DryRun) { $Killed = $true }
        }
    }
    if ($Killed) { Start-Sleep -Milliseconds 800 }
}

function Invoke-Winget {
    param([string[]]$ArgList)
    $Proc = Start-Process -FilePath "winget" -ArgumentList $ArgList -NoNewWindow -Wait -PassThru
    return $Proc.ExitCode
}

function Invoke-Chocolatey {
    param([string]$AppId)
    try {
        choco install $AppId -y --limit-output | Out-Null
        return 0
    } catch {
        return 1
    }
}

# Winget exit codes that mean "nothing needed to change" - all three
# resolve to Success + AlreadyCurrent below. Kept as one list so the
# pre-retry gate in Smart-Deploy (never force-retry a no-op result) and
# Resolve-WingetExitCode read from the same source of truth.
$Script:WingetAlreadyCurrentCodes = @(-1978335212, -1978335153, -1978335189)

# Winget exit codes that mean "this process's CURRENT elevation state is
# categorically wrong for this operation" - retrying with --force changes
# nothing (it's not a lock/transient failure, it's a hard refusal baked
# into the installer manifest or into winget itself), so these share the
# same no-retry gate as WingetAlreadyCurrentCodes below. Resolve-WingetExitCode
# flags them with ElevationConflict = $true so Smart-Deploy reports them as
# Skipped (with a "relaunch at the other elevation level" instruction)
# instead of Failed.
$Script:WingetElevationConflictCodes = @(-1978335146, -1978335107, -1978335207)

function Resolve-WingetExitCode {
    <#
    .SYNOPSIS
        Translates a winget process exit code into Success/AlreadyCurrent/
        Message. Every non-generic code below was cross-checked against
        winget-cli's own AppInstallerErrors.h (FACILITY_WINGET, 0x8A15xxxx)
        - a prior version of this function had three of these mapped to the
        wrong meaning (copied from an unverified forum post, near as we can
        tell), including treating an installer HASH MISMATCH as a silent
        success. That is a security-relevant bug (a corrupted or tampered
        download reported as "completed successfully"), not just a wording
        nitpick, so it's called out explicitly rather than folded in quietly.
    #>
    param([int]$Code)
    switch ($Code) {
        0            { return @{ Success = $true;  AlreadyCurrent = $false; Message = "Completed successfully." } }
        3010         { return @{ Success = $true;  AlreadyCurrent = $false; Message = "Completed successfully. A reboot is recommended." } }
        # 0x8A150014 NO_APPLICATIONS_FOUND - `winget upgrade --id X --exact`
        # searches the AVAILABLE-UPGRADES list; an up-to-date package isn't
        # in it, so the id lookup finds nothing. The common real-world
        # "already current" signal for upgrades.
        -1978335212  { return @{ Success = $true;  AlreadyCurrent = $true;  Message = "Already up to date." } }
        # 0x8A15004F UPGRADE_VERSION_NOT_NEWER - resolved candidate isn't
        # newer than what's installed. Also "already current", not a file
        # lock (that was this code's previous, incorrect label).
        -1978335153  { return @{ Success = $true;  AlreadyCurrent = $true;  Message = "Already up to date." } }
        # 0x8A15002B UPDATE_NOT_APPLICABLE - same "nothing to do" family.
        # Also not "package not found" (that was this code's previous,
        # incorrect label).
        -1978335189  { return @{ Success = $true;  AlreadyCurrent = $true;  Message = "Already up to date." } }
        # 0x8A150011 INSTALLER_HASH_MISMATCH - the downloaded installer's
        # hash didn't match the manifest. A real failure (possible
        # corruption or tampering) - previously mislabeled "no applicable
        # upgrade" and treated as a silent success.
        -1978335215  { return @{ Success = $false; AlreadyCurrent = $false; Message = "Installer hash didn't match the expected value (corrupted or tampered download). Try again." } }
        # 0x8A150006 SHELLEXEC_INSTALL_FAILED - winget launched the
        # installer, but the installer itself exited non-zero. Common with
        # MSYS2 when a previous MSYS2/MinGW terminal is still open (locked
        # files) - Stop-LockingProcesses now covers it (see LockProcessMap).
        -1978335226  { return @{ Success = $false; AlreadyCurrent = $false; Message = "The installer itself reported a failure - often caused by a previous install still open (close any MSYS2/MinGW terminals for GCC, for example) or a locked file. Try again after closing related apps." } }
        # 0x8A150056 INSTALLER_PROHIBITS_ELEVATION - the installer's own
        # manifest refuses to run under an Administrator token (Spotify is
        # the well-known example). Not fixable with --scope or --force;
        # the only fix is running winget at the OTHER elevation level.
        -1978335146  { return @{ Success = $false; AlreadyCurrent = $false; ElevationConflict = $true; Message = "This app's installer refuses to run while Pulse is elevated (Administrator). Use Pulse's GUI without elevating to install it (the interactive console menu always runs elevated, so it can't install this app either)." } }
        # 0x8A15007D ADMIN_CONTEXT_REPAIR_PROHIBITED - same family as
        # above, scoped to a repair/modify path specifically.
        -1978335107  { return @{ Success = $false; AlreadyCurrent = $false; ElevationConflict = $true; Message = "This app's repair/modify path is blocked while Pulse is elevated (Administrator). Use Pulse's GUI without elevating instead." } }
        # 0x8A150019 COMMAND_REQUIRES_ADMIN - the reverse case: this
        # operation genuinely needs elevation and Pulse doesn't have it.
        -1978335207  { return @{ Success = $false; AlreadyCurrent = $false; ElevationConflict = $true; Message = "This app requires Administrator rights to install. Click 'Run as Administrator' in the Pulse sidebar to relaunch elevated, then retry." } }
        1602         { return @{ Success = $false; AlreadyCurrent = $false; Message = "Installer was cancelled." } }
        1            { return @{ Success = $false; AlreadyCurrent = $false; Message = "Generic failure (Exit Code 1)." } }
        default      { return @{ Success = $false; AlreadyCurrent = $false; Message = "Unhandled exit code ($Code)." } }
    }
}

function Open-FallbackUrl {
    param($AppId, $AppName)
    $url = $Script:DownloadUrls[$AppId]
    if ($Script:NonInteractive -or $Script:DryRun) {
        # GUI task / dry-run: NEVER pop a browser mid-silent-run. Log the
        # link so the user can find it in the operation log instead.
        if ($url) { Write-Log "FALLBACK-URL for ${AppName}: $url" }
        else      { Write-Log "FALLBACK-URL for ${AppName}: no official URL mapped." }
        return
    }
    if ($url) {
        Write-Info "Opening official download page: $url"
        Start-Process $url
    } else {
        Write-Info "No official URL mapped. Opening search..."
        Start-Process "https://www.google.com/search?q=$AppName download"
    }
}

# ============================================================
#  LOCAL INSTALLER RUNNER (Path C of the generic Tool Install Wizard)
# ============================================================
function Invoke-GuiLocalInstall {
    <#
    .SYNOPSIS
        Runs an installer file the user already downloaded and picked
        through widgets.ToolInstallWizardDialog's Path C (task
        InstallLocalFile). Generic by design - unlike Office's ODT flow,
        "run this installer the user pointed at" needs no tool-specific
        knowledge: .msi goes through msiexec /i, everything else runs
        directly. Most installers self-elevate via their own manifest if
        they need to (Windows shows that UAC prompt regardless of this
        hidden/no-window parent process), so this never forces elevation
        itself - exactly like a user double-clicking the file manually.
    #>
    param([Parameter(Mandatory = $true)][string]$FilePath)

    if (-not (Test-Path -Path $FilePath -PathType Leaf)) {
        Write-ErrorX "Installer file not found: $FilePath"
        return $false
    }

    if (Test-DryRun "Run local installer '$FilePath'") { return $true }

    Write-Info "Running installer: $FilePath"
    try {
        $Ext = [System.IO.Path]::GetExtension($FilePath).ToLowerInvariant()
        if ($Ext -eq ".msi") {
            $Proc = Start-Process -FilePath "msiexec.exe" -ArgumentList @("/i", ('"' + $FilePath + '"')) -Wait -PassThru
        } else {
            $Proc = Start-Process -FilePath $FilePath -Wait -PassThru
        }
        if ($Proc.ExitCode -eq 0 -or $Proc.ExitCode -eq 3010) {
            Write-Success "Installer finished (exit code $($Proc.ExitCode))."
            return $true
        } else {
            Write-ErrorX "Installer exited with code $($Proc.ExitCode)."
            return $false
        }
    } catch {
        Write-ErrorX "Could not run the installer: $($_.Exception.Message)"
        return $false
    }
}

# ============================================================
#  SMART DEPLOY (the one true install path)
# ============================================================
function Smart-Deploy {
    param(
        [string]$AppId,
        [string]$AppName,
        [switch]$Bulk,
        [ValidateSet('auto','manual')]
        [string]$BulkMethod
    )

    if ([string]::IsNullOrWhiteSpace($AppId)) { return @{Status='Skipped'; Message='Empty AppId'} }

    # Lazy winget bootstrap: only software deployment pays for it. Skipped in
    # dry-run - Ensure-Winget itself refuses to download during -WhatIf.
    if (-not (Is-StoreApp $AppId)) { Ensure-Winget | Out-Null }

    if (Is-StoreApp $AppId) {
        Write-Host ""
        Write-StatusPanel -Label "STORE APP" -Text $AppName

        $InstalledVer = Get-InstalledVersion -AppId $AppId -AppName $AppName
        $LatestVer    = Get-LatestVersion -AppId $AppId

        if ($InstalledVer -and ($InstalledVer -eq $LatestVer -or $LatestVer -eq "Store")) {
            Write-AlreadyOK "$AppName -> already installed (v$InstalledVer) - skipped."
            return @{Status='Success'; AlreadyCurrent=$true; Message='Already installed'}
        }

        if ($Script:DryRun) {
            if ($InstalledVer) {
                if (Test-DryRun "winget upgrade --id $AppId ($AppName) via --source msstore, silent") { }
            } else {
                Write-Info "[WHATIF] $AppName is a Microsoft Store app - a real run would require the Store (skipped)."
            }
            return @{Status='Success'; Message='Dry-run (no change)'}
        }

        if ($InstalledVer) {
            # An update IS available (the AlreadyCurrent short-circuit above
            # only returns when there ISN'T one) - unlike a brand-new Store
            # install, updating an app already on the machine needs no
            # first-run Store consent UI, so this can run through winget's
            # msstore source exactly like a normal silent upgrade - the
            # same single unified pass Update Center uses for Win32 apps.
            Write-Warn "$AppName update available (Store): $InstalledVer -> $LatestVer"
            if ($Bulk) {
                if ($BulkMethod -eq 'manual') {
                    Write-Info "Opening Store page for $AppName..."
                    Start-Process "ms-windows-store://pdp/?ProductId=$AppId"
                    return @{Status='Success'; Message='Store opened'}
                }
                # 'auto' falls through to the silent winget update below.
            } elseif ($Script:NonInteractive) {
                Write-Info "GUI mode: proceeding with silent winget update (msstore source)."
            } else {
                Write-Host "   y = Update via winget (silent, msstore source)" -ForegroundColor Yellow
                Write-Host "   n = Skip this app only" -ForegroundColor Yellow
                Write-Host "   b = Back to category" -ForegroundColor Yellow
                Write-Host "   q = Quit to main menu" -ForegroundColor Yellow
                $choice = Read-Choice -Prompt "   Choose (y/n/b/q)" -Valid @('y','n','b','q')
                switch ($choice) {
                    'q' { return @{Status='Quit'; Message='User quit to main menu'} }
                    'b' { return @{Status='Back'; Message='User returned to category'} }
                    'n' { Write-Info "Bypassed $AppName."; return @{Status='Skipped'; Message='User skipped'} }
                    'y' { }
                }
            }

            Ensure-Winget | Out-Null
            if (-not $global:WingetAvailable) {
                Write-ErrorX "$AppName failed: winget is unavailable, so this Microsoft Store update can't be applied."
                return @{Status='Failed'; Message='winget unavailable'}
            }

            Write-Info "Updating $AppName via winget (Microsoft Store source)..."
            $Code = Invoke-Winget -ArgList @("upgrade", "--id", $AppId, "--exact", "--source", "msstore", "--accept-source-agreements", "--accept-package-agreements", "--disable-interactivity")
            $Result = Resolve-WingetExitCode -Code $Code
            if ($Result.Success) {
                if ($Result.AlreadyCurrent) { Write-AlreadyOK "$AppName -> $($Result.Message) - skipped." }
                else { Write-Success "$AppName -> $($Result.Message)" }
                return @{Status='Success'; AlreadyCurrent=$Result.AlreadyCurrent; Message=$Result.Message}
            } else {
                Write-ErrorX "$AppName failed: $($Result.Message)"
                return @{Status='Failed'; Message=$Result.Message}
            }
        }

        if ($Bulk) {
            if ($BulkMethod -eq 'manual') {
                Write-Info "Opening Store page for $AppName..."
                Start-Process "ms-windows-store://pdp/?ProductId=$AppId"
                return @{Status='Success'; Message='Store opened'}
            } else {
                Write-Warn "$AppName is a Store app and cannot be installed via winget. Skipping."
                return @{Status='Skipped'; Message='Store app'}
            }
        }

        if ($Script:NonInteractive) {
            # GUI task: no console to prompt on and no silent install path
            # for Store apps - skip cleanly instead of hanging on Read-Choice.
            Write-Warn "$AppName is a Microsoft Store app - skipped in GUI mode."
            return @{Status='Skipped'; Message='Store app (GUI)'}
        }

        Write-Host "   m = Open Microsoft Store page" -ForegroundColor Yellow
        Write-Host "   n = Skip this app only" -ForegroundColor Yellow
        Write-Host "   b = Back to category" -ForegroundColor Yellow
        Write-Host "   q = Quit to main menu" -ForegroundColor Yellow
        $choice = Read-Choice -Prompt "   Choose (m/n/b/q)" -Valid @('m','n','b','q')
        switch ($choice) {
            'q' { return @{Status='Quit'; Message='User quit to main menu'} }
            'b' { return @{Status='Back'; Message='User returned to category'} }
            'm' {
                Write-Info "Launching Microsoft Store..."
                Start-Process "ms-windows-store://pdp/?ProductId=$AppId"
                Write-Success "Store page opened."
                return @{Status='Success'; Message='Store opened'}
            }
            default { return @{Status='Skipped'; Message='Skipped'} }
        }
    }

    Write-Host ""
    Write-StatusPanel -Label "TARGET" -Text $AppName

    $CurrentVersion = Get-InstalledVersion -AppId $AppId -AppName $AppName
    $LatestVersion  = Get-LatestVersion -AppId $AppId
    # See $Script:AlwaysForceReinstallAppIds in 01-Catalogs.ps1: some
    # AppIds (Microsoft.Edge) can report a "current" version through this
    # exact same version probe even when the payload Pulse actually cares
    # about was just removed - the fast-path skip below has to be bypassed
    # for them, or a reinstall silently does nothing.
    $ForceReinstall = $Script:AlwaysForceReinstallAppIds -contains $AppId

    if ($CurrentVersion) {
        if (($CurrentVersion -eq $LatestVersion -or $LatestVersion -eq "Unknown") -and -not $ForceReinstall) {
            Write-AlreadyOK "$AppName -> already up to date (v$CurrentVersion) - skipped."
            return @{Status='Success'; AlreadyCurrent=$true; Message='Already up to date'}
        }
        if ($ForceReinstall -and ($CurrentVersion -eq $LatestVersion -or $LatestVersion -eq "Unknown")) {
            Write-Warn "$AppName reports as already current, but always gets a forced reinstall (see AlwaysForceReinstallAppIds)."
        } else {
            Write-Warn "$AppName update available: $CurrentVersion -> $LatestVersion"
        }
    } else {
        Write-Warn "$AppName is not installed. (Latest: $LatestVersion)"
    }

    # -WhatIf: report the exact action a real run would take, then stop.
    if ($Script:DryRun) {
        $Verb = if ($CurrentVersion) { "upgrade" } else { "install" }
        if (Test-DryRun "winget $Verb --id $AppId ($AppName), silent, with agreements accepted") { }
        return @{Status='Success'; Message='Dry-run (no change)'}
    }

    if ($Bulk) {
        if ($BulkMethod -eq 'manual') {
            Open-FallbackUrl $AppId $AppName
            return @{Status='Success'; Message='Manual URL (bulk)'}
        }
    } elseif ($Script:NonInteractive) {
        # GUI task: the card click IS the confirmation - fall through to
        # the silent winget deployment without prompting.
        Write-Info "GUI mode: proceeding with silent winget deployment."
    } else {
        Write-Host "   y = Auto install via winget (silent)" -ForegroundColor Yellow
        Write-Host "   m = Open official website (manual download)" -ForegroundColor Yellow
        Write-Host "   n = Skip this app only" -ForegroundColor Yellow
        Write-Host "   b = Back to category" -ForegroundColor Yellow
        Write-Host "   q = Quit to main menu" -ForegroundColor Yellow
        $choice = Read-Choice -Prompt "   Choose (y/m/n/b/q)" -Valid @('y','m','n','b','q')
        switch ($choice) {
            'q' { return @{Status='Quit'; Message='User quit to main menu'} }
            'b' { return @{Status='Back'; Message='User returned to category'} }
            'n' { Write-Info "Bypassed $AppName."; return @{Status='Skipped'; Message='User skipped'} }
            'm' { Open-FallbackUrl $AppId $AppName; return @{Status='Success'; Message='Manual URL'} }
            'y' { }
        }
    }

    if (-not $global:WingetAvailable) {
        if ($global:ChocolateyAvailable) {
            Write-Info "Installing via Chocolatey..."
            $code = Invoke-Chocolatey $AppId
            if ($code -eq 0) { Write-Success "$AppName installed via Chocolatey."; return @{Status='Success'; Message='Chocolatey'} }
            else { Write-ErrorX "Chocolatey failed."; return @{Status='Failed'; Message='Chocolatey failed'} }
        } else {
            Write-ErrorX "No package manager available."
            Open-FallbackUrl $AppId $AppName
            return @{Status='Failed'; Message='No package manager'}
        }
    }

    # Known elevation-prohibited apps (Spotify et al - see
    # $Script:KnownElevationProhibitedAppIds) are a guaranteed
    # INSTALLER_PROHIBITS_ELEVATION failure while Pulse runs elevated.
    # Skip the doomed winget call entirely instead of burning an attempt +
    # a force retry that would only reproduce the identical failure.
    if ($Script:IsAdminSession -and $Script:KnownElevationProhibitedAppIds -contains $AppId) {
        $Message = "This app's installer refuses to run while Pulse is elevated (Administrator). Use Pulse's GUI without elevating to install it (the interactive console menu always runs elevated, so it can't install this app either)."
        Write-Warn "$AppName skipped: $Message"
        return @{Status='Skipped'; Message=$Message}
    }

    Stop-LockingProcesses -AppId $AppId
    Write-Info "Running winget - live progress:"
    if ($ForceReinstall) {
        # AlwaysForceReinstallAppIds bypass "upgrade" entirely - an upgrade
        # call has nothing to do against a version winget considers already
        # current, which is exactly the broken state this list exists to
        # route around. A forced install reliably re-lays the package
        # either way.
        $Code = Invoke-Winget -ArgList @("install", "--id", $AppId, "--exact", "--accept-source-agreements", "--accept-package-agreements", "--force", "--disable-interactivity")
    } elseif ($CurrentVersion) {
        $Code = Invoke-Winget -ArgList @("upgrade", "--id", $AppId, "--exact", "--include-unknown", "--accept-source-agreements", "--accept-package-agreements", "--disable-interactivity")
    } else {
        $Code = Invoke-Winget -ArgList @("install", "--id", $AppId, "--exact", "--accept-source-agreements", "--accept-package-agreements", "--disable-interactivity")
    }

    if ($Code -ne 0 -and $Script:WingetAlreadyCurrentCodes -notcontains $Code -and $Script:WingetElevationConflictCodes -notcontains $Code) {
        # Never force-retry a code that means "nothing to do" or "the
        # elevation state itself is wrong" - force changes neither, so it
        # would either force an unnecessary reinstall or just reproduce
        # the identical elevation failure a second time.
        Write-Warn "First attempt failed. Retrying with force flags..."
        Start-Sleep -Seconds 3
        if ($CurrentVersion -and -not $ForceReinstall) {
            $Code = Invoke-Winget -ArgList @("upgrade", "--id", $AppId, "--exact", "--include-unknown", "--accept-source-agreements", "--accept-package-agreements", "--force", "--disable-interactivity")
        } else {
            $Code = Invoke-Winget -ArgList @("install", "--id", $AppId, "--exact", "--accept-source-agreements", "--accept-package-agreements", "--force", "--disable-interactivity")
        }
    }

    $Result = Resolve-WingetExitCode -Code $Code

    if ($Result.Success) {
        if ($Result.AlreadyCurrent) {
            Write-AlreadyOK "$AppName -> $($Result.Message) - skipped."
        } else {
            Write-Success "$AppName -> $($Result.Message)"
        }
        if ($Script:DevAppPaths.ContainsKey($AppId)) { Register-DevPath -AppId $AppId -AppName $AppName }
        if (-not $Result.AlreadyCurrent) {
            Test-DevDependencySuggestion -AppId $AppId
        }
        return @{Status='Success'; AlreadyCurrent=$Result.AlreadyCurrent; Message=$Result.Message}
    } elseif ($Result.ElevationConflict) {
        # Not a real failure - Pulse's CURRENT elevation state is simply
        # wrong for this one app. Write-Warn (not Write-ErrorX) so this
        # never bumps $Script:SessionFailCount and flips an otherwise
        # clean bulk run to an ERROR verdict (see Complete-GuiTask).
        Write-Warn "$AppName skipped: $($Result.Message)"
        return @{Status='Skipped'; Message=$Result.Message}
    } else {
        Write-ErrorX "$AppName failed: $($Result.Message)"
        if (-not $Bulk -and -not $Script:NonInteractive) {
            $openFallback = Read-Choice -Prompt "   Auto install failed. Open official website? (y/n)" -Valid @('y','n')
            if ($openFallback -eq 'y') { Open-FallbackUrl $AppId $AppName }
        } else {
            # Bulk/GUI: Open-FallbackUrl is itself NonInteractive-aware
            # (logs the URL instead of opening a browser in GUI mode).
            Open-FallbackUrl $AppId $AppName
        }
        return @{Status='Failed'; Message=$Result.Message}
    }
}

# ============================================================
#  HARDWARE MATCHING (GPU / motherboard vendor apps)
# ============================================================
function Hardware-Check {
    $GPU = Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name | Select-Object -First 1
    $GPUApp = if ($GPU -match "NVIDIA") { "Nvidia.GeForceExperience" }
              elseif ($GPU -match "AMD|Radeon") { "AdvancedMicroDevices.Adrenalin" }
              elseif ($GPU -match "Intel") { "Intel.IntelGraphicsCommandCenter" } else { "" }

    $Mobo = Get-CimInstance Win32_BaseBoard | Select-Object -ExpandProperty Manufacturer
    $MoboApp = if ($Mobo -match "ASUS") { "Asus.ArmouryCrate" }
               elseif ($Mobo -match "Micro-Star|MSI") { "Micro-Star.MSICenter" }
               elseif ($Mobo -match "Gigabyte") { "Gigabyte.ControlCenter" }
               elseif ($Mobo -match "ASRock") { "ASRock.AppShop" } else { "" }

    return @{ GPUApp = $GPUApp; MoboApp = $MoboApp; MoboName = $Mobo; GPUName = $GPU }
}

function Get-DisplayRefreshRate {
    try {
        $Rates = Get-CimInstance Win32_VideoController -ErrorAction Stop |
                 Where-Object { $_.CurrentRefreshRate -gt 0 } |
                 Select-Object -ExpandProperty CurrentRefreshRate
        return $Rates
    } catch {
        return $null
    }
}

# ============================================================
#  CATEGORY PROCESSOR (interactive console flow)
# ============================================================
function Process-AppCategory {
    param($AppList, $CategoryName)

    Write-SectionHeader $CategoryName

    if ($Script:LastBulkChoice) {
        Write-Host "   Last bulk choice: $($Script:LastBulkChoice.Method). Reuse it for this category?" -ForegroundColor Yellow
        if (Ask-User "Reuse Last Bulk Mode" "Applies the '$($Script:LastBulkChoice.Method)' method to every app in '$CategoryName' without asking again.") {
            Initialize-WingetBatchCache
            try {
                foreach ($App in $AppList) {
                    $res = Smart-Deploy -AppId $App[0] -AppName $App[1] -Bulk -BulkMethod $Script:LastBulkChoice.Method
                    if ($res.Status -eq 'Quit') { break }
                }
            } finally {
                $Script:WingetBatchCache = $null
            }
            return "OK"
        }
    }

    Write-Host "   y = Bulk auto (winget install all silently)" -ForegroundColor Yellow
    Write-Host "   m = Bulk manual (open official websites for all)" -ForegroundColor Yellow
    Write-Host "   n = Choose individually" -ForegroundColor Yellow
    Write-Host "   b = Back to previous menu" -ForegroundColor Yellow
    Write-Host "   q = Quit to main menu" -ForegroundColor Yellow
    $bulkChoice = Read-Choice -Prompt "   Choose (y/m/n/b/q)" -Valid @('y','m','n','b','q')
    if ($bulkChoice -eq 'q') { return "QUIT" }
    if ($bulkChoice -eq 'b') { return "BACK" }

    if ($bulkChoice -eq 'y' -or $bulkChoice -eq 'm') {
        $method = if ($bulkChoice -eq 'y') { 'auto' } else { 'manual' }
        $Script:LastBulkChoice = @{Method=$method}

        $results = @{}
        Initialize-WingetBatchCache
        try {
            foreach ($App in $AppList) {
                $res = Smart-Deploy -AppId $App[0] -AppName $App[1] -Bulk -BulkMethod $method
                if ($res.Status -eq 'Quit') { break }
                $results[$App[1]] = $res
            }
        } finally {
            $Script:WingetBatchCache = $null
        }

        Write-Divider
        $success = ($results.GetEnumerator() | Where-Object { $_.Value.Status -eq 'Success' -and -not $_.Value.AlreadyCurrent }).Count
        $current = ($results.GetEnumerator() | Where-Object { $_.Value.Status -eq 'Success' -and $_.Value.AlreadyCurrent }).Count
        $failed  = ($results.GetEnumerator() | Where-Object { $_.Value.Status -eq 'Failed' }).Count
        $skipped = ($results.GetEnumerator() | Where-Object { $_.Value.Status -eq 'Skipped' }).Count
        Write-Info "Bulk summary for '$CategoryName': $success installed, $current already up to date, $failed failed, $skipped skipped."
        return "OK"
    }

    foreach ($App in $AppList) {
        $result = Smart-Deploy $App[0] $App[1]
        if ($result.Status -eq 'Quit') {
            Write-Warn "Exiting '$CategoryName' and returning to main menu."
            return "QUIT"
        }
        if ($result.Status -eq 'Back') {
            Write-Warn "Returning to category selection."
            return "BACK"
        }
    }
    return "OK"
}
