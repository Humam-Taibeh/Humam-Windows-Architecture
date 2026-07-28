#Requires -Version 5.1
<#
.SYNOPSIS
    11-StateProbe.ps1 - read-only "is this tweak currently applied?" probe.

.DESCRIPTION
    Pulse could always APPLY a tweak but never report whether it was
    already in effect. Every card was fire-and-forget: the only way to
    answer "did I already do this?" was to run the operation again and
    read the log. This module answers it directly, so the GUI can show an
    "Applied" state on the cards whose effect is readable.

    HARD CONTRACT - this file is READ-ONLY:
      * It MUST NOT write to the registry, touch services, create restore
        points, or mutate anything. It is invoked on a timer-ish basis by
        the GUI (on launch and after every task), so a mutating probe
        would silently re-apply tweaks behind the user's back.
      * Every check is wrapped so that a missing key, a policy-locked
        hive or an access-denied read yields $null ("unknown"), never an
        exception and never a false "applied". Unknown is a legitimate,
        honest third state - the GUI renders nothing for it rather than
        guessing.
      * It requires NO elevation. Checks that would need admin to read are
        reported as $null rather than escalating; an unelevated Pulse
        still gets a useful answer for every HKCU-based tweak.

    States returned per key: $true (applied) / $false (not applied) /
    $null (unknown - unreadable on this machine or in this session).
#>

function Test-RegValueEquals {
    <# $true when EVERY (Path, Name, Value) triple matches, $false when any
       readable one differs, $null when nothing could be read at all (the
       key doesn't exist / access denied) - so "unknown" never masquerades
       as "not applied", which would show a misleading un-Applied card. #>
    param([Parameter(Mandatory)][array]$Checks)

    $readAny = $false
    foreach ($check in $Checks) {
        $value = $null
        try {
            if (Test-Path $check.Path -ErrorAction Stop) {
                $item = Get-ItemProperty -Path $check.Path -Name $check.Name -ErrorAction Stop
                $value = $item.($check.Name)
            }
        } catch {
            continue     # unreadable entry - fall through to the next one
        }
        if ($null -eq $value) { continue }
        $readAny = $true
        if ([string]$value -ne [string]$check.Value) { return $false }
    }
    if (-not $readAny) { return $null }
    return $true
}

function Get-PulseTweakState {
    <# The full applied-state map the GUI consumes. Keys are GUI TASK NAMES
       (menu_structure.py's `task` values), so the frontend can look a card
       up directly with no translation table to drift out of sync. #>

    $state = [ordered]@{}

    # -- Global Dark Mode ------------------------------------------------
    $state["DarkMode"] = Test-RegValueEquals -Checks @(
        @{ Path = "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"; Name = "AppsUseLightTheme";   Value = 0 },
        @{ Path = "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"; Name = "SystemUsesLightTheme"; Value = 0 }
    )

    # -- Mouse acceleration ----------------------------------------------
    $state["DisableMouseAccel"] = Test-RegValueEquals -Checks @(
        @{ Path = "HKCU:\Control Panel\Mouse"; Name = "MouseSpeed";      Value = 0 },
        @{ Path = "HKCU:\Control Panel\Mouse"; Name = "MouseThreshold1"; Value = 0 },
        @{ Path = "HKCU:\Control Panel\Mouse"; Name = "MouseThreshold2"; Value = 0 }
    )

    # -- Minimalist taskbar (Windows 11 only) ----------------------------
    if ($Script:OSBuild -ge 22000) {
        $state["MinimalistTaskbar"] = Test-RegValueEquals -Checks @(
            @{ Path = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"; Name = "TaskbarAl"; Value = 0 },
            @{ Path = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"; Name = "TaskbarDa"; Value = 0 }
        )
        # Classic context menu: the tweak is the PRESENCE of the CLSID
        # InprocServer32 key with an empty default value, so absence is a
        # definite "not applied" rather than an unknown.
        $classic = "HKCU:\Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}\InprocServer32"
        try {
            if (Test-Path $classic -ErrorAction Stop) {
                $default = (Get-ItemProperty -Path $classic -ErrorAction Stop).'(default)'
                $state["ClassicContextMenu"] = ($default -eq "")
            } else {
                $state["ClassicContextMenu"] = $false
            }
        } catch {
            $state["ClassicContextMenu"] = $null
        }
    } else {
        $state["MinimalistTaskbar"] = $null
        $state["ClassicContextMenu"] = $null
    }

    # -- Game Mode & Game DVR --------------------------------------------
    $state["GameMode"] = Test-RegValueEquals -Checks @(
        @{ Path = "HKCU:\Software\Microsoft\GameBar";     Name = "AutoGameModeEnabled"; Value = 1 },
        @{ Path = "HKCU:\System\GameConfigStore";         Name = "GameDVR_Enabled";     Value = 0 }
    )

    # -- Advertising ID ---------------------------------------------------
    $state["DisableAdvertisingID"] = Test-RegValueEquals -Checks @(
        @{ Path = "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\AdvertisingInfo"; Name = "Enabled"; Value = 0 }
    )

    # -- Activity history (HKLM policy; readable unelevated) -------------
    $state["DisableActivityHistory"] = Test-RegValueEquals -Checks @(
        @{ Path = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\System"; Name = "EnableActivityFeed"; Value = 0 }
    )

    # -- Telemetry: policy value AND the DiagTrack service ---------------
    $telemetryPolicy = Test-RegValueEquals -Checks @(
        @{ Path = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection"; Name = "AllowTelemetry"; Value = 0 }
    )
    $diagTrackOff = $null
    try {
        $svc = Get-Service -Name "DiagTrack" -ErrorAction Stop
        $diagTrackOff = ($svc.StartType -eq "Disabled")
    } catch {
        # service genuinely absent => nothing left to disable
        $diagTrackOff = $true
    }
    if ($null -eq $telemetryPolicy) {
        $state["DisableTelemetry"] = $null
    } else {
        $state["DisableTelemetry"] = ($telemetryPolicy -and $diagTrackOff)
    }

    # -- Hibernation (mutually exclusive pair) ---------------------------
    # hiberfil.sys is not enumerable without admin on some systems, so a
    # failed probe is reported as unknown rather than "off".
    try {
        $hiberOn = Test-Path "$env:SystemDrive\hiberfil.sys" -ErrorAction Stop
        $state["DisableHibernation"] = (-not $hiberOn)
        $state["EnableHibernation"] = $hiberOn
    } catch {
        $state["DisableHibernation"] = $null
        $state["EnableHibernation"] = $null
    }

    # -- Ultimate power plan: active scheme carries the Pulse name -------
    try {
        $active = (powercfg /getactivescheme 2>$null | Out-String)
        if ([string]::IsNullOrWhiteSpace($active)) {
            $state["UltimatePowerPlan"] = $null
        } else {
            $state["UltimatePowerPlan"] = ($active -match "Pulse|Ultimate Performance|Humam Ultimate")
        }
    } catch {
        $state["UltimatePowerPlan"] = $null
    }

    # ====================================================================
    #  REMOVAL TASKS (v10.1)
    #
    #  These differ from every probe above in the direction they read: the
    #  tweaks ask "was this setting written?", whereas a removal asks "is
    #  this component GONE?". Applied therefore means ABSENT, and the
    #  probe must be careful not to report a machine that simply never had
    #  the component as though Pulse had removed it — which is fine and
    #  honest here, because from the card's point of view "Edge is not on
    #  this system" is exactly what the Remove Edge card promises.
    #
    #  DELIBERATE COUPLING: Test-MicrosoftEdgeInstalled and
    #  Test-OneDriveInstalled are reused from 06-Tweaks.ps1 rather than
    #  reimplemented. Both are pure read-only presence checks (Test-Path /
    #  Get-Process / registry reads), and core.ps1 dot-sources modules in
    #  sorted order, so 06 is always loaded before this file. Duplicating
    #  the detection logic would be the greater risk: two definitions of
    #  "is Edge installed" WILL drift, and the removal path and the badge
    #  would then disagree on screen. If either helper ever gains a side
    #  effect it breaks THIS module's read-only contract — that is what
    #  tests/test_contract.py::test_state_probe_is_read_only pins.
    # ====================================================================

    # -- Edge / OneDrive: absent means removed ---------------------------
    try {
        $state["RemoveEdge"] = (-not (Test-MicrosoftEdgeInstalled))
    } catch {
        $state["RemoveEdge"] = $null
    }
    try {
        $state["RemoveOneDrive"] = (-not (Test-OneDriveInstalled))
    } catch {
        $state["RemoveOneDrive"] = $null
    }

    # -- Windows.old ------------------------------------------------------
    # Test-Path on the folder itself needs no elevation even though
    # ENUMERATING it does, so this stays honest for an unelevated session.
    try {
        $state["RemoveWindowsOld"] = (-not (Test-Path "$env:SystemDrive\Windows.old" -ErrorAction Stop))
    } catch {
        $state["RemoveWindowsOld"] = $null
    }

    # -- Bloatware: none of the catalog's packages still installed -------
    # Get-AppxPackage (no -AllUsers) reports the CURRENT user's packages
    # and needs no elevation. Applied means the intersection with
    # $Script:BloatApps is empty. A machine where the catalog was never
    # applied but the packages were never present either reads as applied,
    # which is the truthful answer to "is this bloatware on my system?".
    try {
        if (-not $Script:BloatApps) {
            $state["RemoveBloatware"] = $null      # catalog missing - can't judge
        } else {
            $installed = @(Get-AppxPackage -ErrorAction Stop | Select-Object -ExpandProperty Name -ErrorAction Stop)
            $remaining = @($Script:BloatApps | Where-Object { $installed -contains $_ })
            $state["RemoveBloatware"] = ($remaining.Count -eq 0)
        }
    } catch {
        # Appx subsystem unavailable / policy-locked / Server Core
        $state["RemoveBloatware"] = $null
    }

    # -- Full privacy pass: a COMPOSITE of its four constituent tasks ----
    # ApplyAllPrivacy runs Remove-Bloatware, Disable-Telemetry,
    # Disable-AdvertisingID and Disable-ActivityHistory (see
    # 30-GuiDispatcher.ps1), so it has no state of its own to read; it is
    # applied exactly when all four are. Three-state logic is preserved
    # rather than collapsed: one definite $false means the pass is
    # definitely incomplete, but an unreadable component means UNKNOWN —
    # never let a $null quietly count as "not applied" and show a card as
    # un-applied when the truth is that we could not tell.
    $privacyParts = @(
        $state["RemoveBloatware"], $state["DisableTelemetry"],
        $state["DisableAdvertisingID"], $state["DisableActivityHistory"]
    )
    if ($privacyParts -contains $false) {
        $state["ApplyAllPrivacy"] = $false
    } elseif ($privacyParts | Where-Object { $null -eq $_ }) {
        $state["ApplyAllPrivacy"] = $null
    } else {
        $state["ApplyAllPrivacy"] = $true
    }

    # -- NOT PROBED: NetworkOptimization ---------------------------------
    # Deliberately absent, and it must stay absent. The task runs
    # `ipconfig /flushdns`, `netsh winsock reset` and `netsh int ip reset`
    # (06-Tweaks.ps1). Those are TRANSIENT operations: they leave no
    # durable, readable marker distinguishing "the stack was reset" from
    # "the stack is at defaults", and the reset only takes effect after a
    # reboot anyway. Any probe for it would be a guess dressed up as a
    # fact, which is precisely what this module's contract forbids — so
    # the card correctly shows no chip at all.

    return $state
}
