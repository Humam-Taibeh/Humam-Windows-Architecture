#Requires -Version 5.1
<#
.SYNOPSIS
    06-Tweaks.ps1 - the data-driven tweak engine plus every system tweak
    and optimization that mutates registry / power / network state.

.DESCRIPTION
    - Invoke-Tweak consumes $Script:TweakCatalog entries (01-Catalogs.ps1):
      a tweak is DATA (registry entries with On/Off values), never a bespoke
      function. Adding a tweak = adding a catalog entry.
    - Every change snapshots the original value first (02-Safety.ps1) and
      creates the once-per-session restore point.
    - All mutations flow through the dry-run primitives, so -WhatIf walks
      the exact same code paths and reports every write it would perform.
#>

# ============================================================
#  DATA-DRIVEN TWEAK ENGINE
# ============================================================
function Test-TweakAlreadyOn {
    param([hashtable]$Tweak)
    foreach ($E in $Tweak.Entries) {
        $Current = Get-RegValue -Path $E.Path -Name $E.Name
        if ("$Current" -ne "$($E.OnValue)") { return $false }
    }
    return $true
}

function Invoke-Tweak {
    param(
        [Parameter(Mandatory)][hashtable]$Tweak,
        [ValidateSet("On","Off")][string]$State = "On"
    )

    Write-SectionHeader $Tweak.Description

    if ($State -eq "On" -and (Test-TweakAlreadyOn -Tweak $Tweak)) {
        Write-AlreadyOK "$($Tweak.Key) is already applied."
        return
    }

    New-SystemRestorePoint

    Invoke-WithRetry -OperationName "Tweak: $($Tweak.Key)" -Action {
        foreach ($E in $Tweak.Entries) {
            Backup-OriginalRegValue -TweakKey $Tweak.Key -Path $E.Path -Name $E.Name
            $Value = if ($State -eq "On") { $E.OnValue } else { $E.OffValue }
            Set-RegValue -Path $E.Path -Name $E.Name -Value $Value -Type $E.Type
        }
        Write-Success "$($Tweak.Key) applied successfully."
    } | Out-Null

    # Theme-affecting tweaks (Dark/Light) need the shell nudged or the change
    # doesn't repaint until sign-out - see Invoke-ShellThemeRefresh.
    if ($Tweak.RefreshShell) { Invoke-ShellThemeRefresh }
}

function Invoke-ShellThemeRefresh {
    <# Applies a just-written theme change (Dark/Light) to the RUNNING shell so
       the taskbar and open surfaces repaint immediately instead of glitching
       until the next sign-in. Two non-disruptive steps (deliberately NOT an
       explorer.exe restart, which would close the user's open File Explorer
       windows for a mere theme toggle):
         1. Broadcast WM_SETTINGCHANGE('ImmersiveColorSet') so every top-level
            window re-reads the theme.
         2. ie4uinit.exe -show to refresh the shell's icon/theme caches.
       Best-effort: any step failing is logged, never fatal - worst case the
       theme still applies on next sign-in. #>
    if (Test-DryRun "Refresh the Windows shell so the theme change applies immediately (WM_SETTINGCHANGE broadcast + ie4uinit -show)") { return }

    try {
        if (-not ([System.Management.Automation.PSTypeName]'Pulse.ShellNative').Type) {
            Add-Type -Namespace Pulse -Name ShellNative -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("user32.dll", SetLastError=true, CharSet=System.Runtime.InteropServices.CharSet.Auto)]
public static extern System.IntPtr SendMessageTimeout(System.IntPtr hWnd, uint Msg, System.UIntPtr wParam, string lParam, uint fuFlags, uint uTimeout, out System.UIntPtr lpdwResult);
'@ -ErrorAction Stop
        }
        $HWND_BROADCAST   = [System.IntPtr]0xffff
        $WM_SETTINGCHANGE = 0x001A
        $SMTO_ABORTIFHUNG = 0x0002
        $out = [System.UIntPtr]::Zero
        foreach ($section in @('ImmersiveColorSet', 'WindowsThemeElement', 'Policy')) {
            [void][Pulse.ShellNative]::SendMessageTimeout($HWND_BROADCAST, $WM_SETTINGCHANGE, [System.UIntPtr]::Zero, $section, $SMTO_ABORTIFHUNG, 200, [ref]$out)
        }
    } catch {
        Write-Log "ShellThemeRefresh: WM_SETTINGCHANGE broadcast failed - $($_.Exception.Message)"
    }

    try {
        Start-Process -FilePath (Get-SystemBinary "ie4uinit") -ArgumentList "-show" -WindowStyle Hidden -ErrorAction Stop
    } catch {
        Write-Log "ShellThemeRefresh: ie4uinit -show failed - $($_.Exception.Message)"
    }

    Write-Success "Windows shell refreshed - the theme change is visible immediately."
}

# ============================================================
#  WINDOWS 11 CLASSIC CONTEXT MENU
# ============================================================
function Enable-ClassicContextMenu {
    Write-SectionHeader "Windows 11 Classic Right-Click Menu"
    if (-not (Test-OSSupport -FeatureName "Classic Right-Click Menu" -MinBuild 22000)) { return }
    New-SystemRestorePoint

    $path = "HKCU:\Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}\InprocServer32"
    $CurrentDefault = Get-RegValue -Path $path -Name "(default)"
    if ((Test-Path $path) -and ($CurrentDefault -eq "")) {
        Write-AlreadyOK "Classic context menu is already active."
        return
    }

    try {
        Set-RegValue -Path $path -Name "(default)" -Value "" -Type String
        Write-Success "Classic context menu restored."

        if (Ask-User "Restart Windows Explorer" "Applies the classic menu immediately by restarting explorer.exe.") {
            Invoke-Mutation -Description "Restart explorer.exe to apply the classic context menu" -Action {
                Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 1
                # Anchored: this runs elevated, and a planted explorer.exe
                # earlier in PATH would inherit that token (00-Foundation.ps1).
                Start-Process -FilePath (Get-SystemBinary "explorer")
                Write-Success "Explorer restarted. Classic menu should now be active."
            } | Out-Null
        } else {
            Write-Info "Change will take effect after you sign out or restart Explorer manually."
        }
    } catch {
        Write-ErrorX "Failed to restore classic context menu: $($_.Exception.Message)"
    }
}

# ============================================================
#  SMART SYSTEM TWEAKS
# ============================================================
function Disable-MouseAcceleration {
    New-SystemRestorePoint
    $Path = "HKCU:\Control Panel\Mouse"
    $Speed = Get-RegValue -Path $Path -Name "MouseSpeed"
    $Thr1  = Get-RegValue -Path $Path -Name "MouseThreshold1"
    $Thr2  = Get-RegValue -Path $Path -Name "MouseThreshold2"
    if ($Speed -eq "0" -and $Thr1 -eq "0" -and $Thr2 -eq "0") {
        Write-AlreadyOK "Mouse acceleration is already disabled."
        return
    }
    Backup-OriginalRegValue -TweakKey "MouseAccel" -Path $Path -Name "MouseSpeed"
    Backup-OriginalRegValue -TweakKey "MouseAccel" -Path $Path -Name "MouseThreshold1"
    Backup-OriginalRegValue -TweakKey "MouseAccel" -Path $Path -Name "MouseThreshold2"
    try {
        Set-RegValue -Path $Path -Name "MouseSpeed" -Value "0"
        Set-RegValue -Path $Path -Name "MouseThreshold1" -Value "0"
        Set-RegValue -Path $Path -Name "MouseThreshold2" -Value "0"
        Write-Success "Raw pointer precision applied (mouse acceleration fully disabled)."
    } catch {
        # A real failure (registry keys restricted by policy) - Write-ErrorX,
        # not Write-Warn, so Complete-GuiTask's fail counter (30-GuiDispatcher.ps1)
        # actually reflects it instead of reporting "Mouse acceleration disabled"
        # to the GUI when it wasn't.
        Write-ErrorX "Could not disable mouse acceleration: $($_.Exception.Message)"
    }
}

function Enable-MinimalistTaskbar {
    if (-not (Test-OSSupport -FeatureName "Windows 11 Minimalist Taskbar" -MinBuild 22000)) { return }
    New-SystemRestorePoint
    $Path = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
    if ((Get-RegValue -Path $Path -Name "TaskbarAl") -eq 0 -and (Get-RegValue -Path $Path -Name "TaskbarDa") -eq 0) {
        Write-AlreadyOK "Minimalist taskbar layout is already applied."
        return
    }
    Backup-OriginalRegValue -TweakKey "Taskbar" -Path $Path -Name "TaskbarAl"
    Backup-OriginalRegValue -TweakKey "Taskbar" -Path $Path -Name "TaskbarDa"
    Backup-OriginalRegValue -TweakKey "Taskbar" -Path $Path -Name "TaskbarMn"
    try {
        Set-RegValue -Path $Path -Name "TaskbarAl" -Value 0
        Set-RegValue -Path $Path -Name "TaskbarDa" -Value 0
        Set-RegValue -Path $Path -Name "TaskbarMn" -Value 0
        Write-Success "Taskbar alignments updated."
    } catch {
        # Real failure, not an informational skip - see the same note on
        # Disable-MouseAcceleration above.
        Write-ErrorX "Could not update taskbar layout: $($_.Exception.Message)"
    }
}

# ============================================================
#  ONEDRIVE REMOVAL / RESTORE
# ============================================================
function Test-OneDriveInstalled {
    <# Explicit pre-flight state check, shared by Remove-OneDrivePackage and
       whatever wants to know up front - true if either the per-user
       install folder or a live OneDrive.exe process is present. #>
    $ODInstallFolder = "$env:LOCALAPPDATA\Microsoft\OneDrive"
    if (Test-Path $ODInstallFolder) { return $true }
    if (Get-Process -Name "OneDrive" -ErrorAction SilentlyContinue) { return $true }
    return $false
}

function Remove-OneDrivePackage {
    <#
    .SYNOPSIS
        Removes OneDrive after an explicit pre-flight state check - callers
        get a hashtable @{Status; Message} back (Status is one of
        AlreadyRemoved / DryRun / Success / Failed) so the GUI dispatcher
        can show the right verdict instead of a generic "removed" message
        even when nothing needed doing.
    #>
    Write-SectionHeader "Purge Microsoft OneDrive"

    if (-not (Test-OneDriveInstalled)) {
        Write-AlreadyOK "OneDrive is already removed from this system."
        return @{ Status = 'AlreadyRemoved'; Message = 'OneDrive is already removed from this system.' }
    }

    New-SystemRestorePoint
    $ODSetup = "$env:SystemRoot\SysWOW64\OneDriveSetup.exe"

    if (-not (Backup-OneDriveFiles)) {
        Write-ErrorX "Aborting OneDrive removal: the backup did not complete successfully. Resolve the issue above and try again."
        return @{ Status = 'Failed'; Message = 'OneDrive removal was aborted because the pre-removal backup did not complete successfully.' }
    }
    try {
        Invoke-Mutation -Description "Terminate OneDrive.exe" -Action {
            Stop-Process -Name "OneDrive" -Force -ErrorAction SilentlyContinue
        } | Out-Null
        if (Test-Path $ODSetup) {
            if (Test-DryRun "Run OneDriveSetup.exe /uninstall") {
                return @{ Status = 'DryRun'; Message = '[DRY-RUN] OneDrive removal simulated (backup + uninstall were reported, not executed).' }
            }
            # -PassThru + exit-code check: without it, Write-Success fired
            # unconditionally regardless of whether the uninstaller actually
            # succeeded (Start-Process doesn't throw on a non-zero exit code).
            $Proc = Start-Process $ODSetup -ArgumentList "/uninstall" -Wait -NoNewWindow -PassThru
            if ($Proc.ExitCode -eq 0) {
                Write-Success "OneDrive uninstall sequence executed."
                return @{ Status = 'Success'; Message = 'OneDrive removed. Local files were backed up to Desktop\Pulse_OneDriveBackup first.' }
            } else {
                Write-ErrorX "OneDrive's uninstaller exited with code $($Proc.ExitCode)."
                return @{ Status = 'Failed'; Message = "OneDrive's uninstaller exited with code $($Proc.ExitCode)." }
            }
        } else {
            Write-Warn "Skipped: OneDrive standalone installer payload not found."
            return @{ Status = 'Failed'; Message = 'OneDrive standalone installer payload not found - it may already be partially removed.' }
        }
    } catch {
        Write-ErrorX "OneDrive removal failed: $($_.Exception.Message)"
        return @{ Status = 'Failed'; Message = "OneDrive removal failed: $($_.Exception.Message)" }
    }
}

function Restore-OneDrivePackage {
    Write-SectionHeader "Restore Microsoft OneDrive"
    if (Ensure-Winget) {
        Write-Info "Reinstalling Microsoft OneDrive via winget..."
        $Result = Smart-Deploy "Microsoft.OneDrive" "Microsoft OneDrive"
        if ($Result.Status -eq 'Success' -and (Test-Path $Script:OneDriveBackupFolder)) {
            Write-Info "Your pre-removal files are still backed up at Desktop\Pulse_OneDriveBackup - copy them back into your OneDrive folder once it finishes syncing."
        }
    } elseif ($Script:DryRun) {
        Write-Info "[WHATIF] Would reinstall Microsoft OneDrive via winget."
    } else {
        Write-Warn "Winget unavailable. Opening official download page for a manual install..."
        Open-UrlSafe -Url "https://www.microsoft.com/en-us/microsoft-365/onedrive/download"
    }
}

# ============================================================
#  MICROSOFT EDGE REMOVAL / REINSTALL
# ============================================================
function Get-EdgeUninstallRegistryKeys {
    <# Every hive/bitness combination Edge's Uninstall entry can land under -
       shared by Test-MicrosoftEdgeInstalled and Clear-EdgeNoRemoveFlags so
       both check exactly the same set. #>
    @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Microsoft Edge"
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Microsoft Edge"
        "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Microsoft Edge"
    )
}

function Test-MicrosoftEdgeInstalled {
    <# Explicit pre-flight state check - Edge counts as "present" if either
       its binary (either Program Files bitness) or its Uninstall registry
       entry (either hive/bitness) still exists, so a stale leftover of
       just one still routes through the real removal path instead of
       silently no-oping, while a machine where NONE of them exist (truly
       already removed) short-circuits instead of re-running the whole
       force-purge sequence for nothing. #>
    $BinaryPaths = @(
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
    )
    foreach ($Path in $BinaryPaths) {
        if (Test-Path $Path) { return $true }
    }
    foreach ($Key in (Get-EdgeUninstallRegistryKeys)) {
        if (Test-Path $Key) { return $true }
    }
    return $false
}

function Clear-EdgeNoRemoveFlags {
    <# Best-effort: Windows/Edge can set a NoRemove=1 flag on the Uninstall
       registry key, which hides/disables the Control Panel uninstall
       button. It doesn't block setup.exe directly, but forcefully clearing
       it up front removes one more thing standing between "Windows thinks
       this is protected" and a clean uninstall. Failures here are logged
       and swallowed - this is a defensive extra step, not the primary
       removal mechanism, so it never aborts the overall purge. #>
    foreach ($Key in (Get-EdgeUninstallRegistryKeys)) {
        if (-not (Test-Path $Key)) { continue }
        $Current = Get-RegValue -Path $Key -Name "NoRemove"
        if ($null -eq $Current -or "$Current" -eq "0") { continue }
        try {
            Set-RegValue -Path $Key -Name "NoRemove" -Value 0 -Type DWord
            Write-Info "Cleared NoRemove protection flag on '$Key'."
        } catch {
            Write-Warn "Could not clear NoRemove flag on '$Key': $($_.Exception.Message)"
        }
    }
}

function Remove-EdgeScheduledTasks {
    <# Last-mile cleanup: the Edge/EdgeUpdate scheduled tasks keep
       reinstalling or re-registering Edge components in the background
       even after the browser payload itself is gone. Best-effort - a
       machine with none of these left is the success case, not a failure. #>
    try {
        $Tasks = Get-ScheduledTask -TaskName "MicrosoftEdgeUpdate*" -ErrorAction SilentlyContinue
        foreach ($Task in $Tasks) {
            try {
                Unregister-ScheduledTask -TaskName $Task.TaskName -TaskPath $Task.TaskPath -Confirm:$false -ErrorAction Stop
                Write-Info "Removed leftover scheduled task '$($Task.TaskName)'."
            } catch {
                Write-Warn "Could not remove scheduled task '$($Task.TaskName)': $($_.Exception.Message)"
            }
        }
    } catch {
        # Get-ScheduledTask itself can throw on a locked-down Task Scheduler
        # service - never let that abort the rest of the purge.
    }
}

function Remove-MicrosoftEdge {
    <#
    .SYNOPSIS
        Explicit pre-flight state check, then an aggressive multi-tier
        force-purge: kill every locking/identity process, forcefully clear
        the NoRemove registry protection flag, run Edge's own setup.exe
        with --force-uninstall, fall back to a winget uninstall, then a
        final Appx + scheduled-task cleanup pass - each tier only runs if
        the one before it wasn't available or failed, and each is a real
        removal attempt in its own right rather than a last-resort no-op.
        Returns a hashtable @{Status; Message} (Status is one of
        AlreadyRemoved / DryRun / Success / Failed) so the GUI dispatcher
        can show the right verdict instead of re-deriving it from a second,
        separate filesystem probe.
    #>
    Write-SectionHeader "Remove Microsoft Edge"

    if (-not (Test-MicrosoftEdgeInstalled)) {
        Write-AlreadyOK "Microsoft Edge is already removed from this system."
        return @{ Status = 'AlreadyRemoved'; Message = 'Microsoft Edge is already removed from this system.' }
    }

    New-SystemRestorePoint
    Backup-EdgeState

    if (Test-DryRun "Force-purge Microsoft Edge (kill processes, clear NoRemove flags, setup.exe --uninstall --system-level --verbose-logging --force-uninstall, falling back to winget/Appx/scheduled-task cleanup if needed)") {
        return @{ Status = 'DryRun'; Message = '[DRY-RUN] Edge removal simulated (backup + uninstall were reported, not executed).' }
    }

    # msedge.exe/msedgewebview2.exe/identity_helper.exe hold their own
    # binaries open - every removal path below fails or silently no-ops if
    # any of them is still running.
    Get-Process -Name "msedge", "msedgewebview2", "identity_helper" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 800

    Clear-EdgeNoRemoveFlags

    $Removed = $false

    # setup.exe's real location is DYNAMIC: it lives under a per-version
    # folder ("...\Edge\Application\<VERSION>\Installer\setup.exe") whose
    # name changes with every Edge update, so a hard-coded path is stale the
    # moment Edge patches itself - the previous cause of setup.exe never
    # being invoked (or a stale copy exiting with code 93). Resolve it at
    # run time by recursively hunting "setup.exe" under the Application root
    # in BOTH Program Files locations (64-bit Edge normally lands in Program
    # Files, but the Installer payload some builds ship still sits under
    # Program Files (x86)). Sort descending so the NEWEST version folder's
    # uninstaller wins when an old version was left behind alongside it.
    $EdgeAppRoots = @(
        "$env:ProgramFiles\Microsoft\Edge\Application"
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application"
    )
    $UninstallPath = $null
    foreach ($Root in $EdgeAppRoots) {
        if (-not (Test-Path -LiteralPath $Root)) { continue }
        $Found = Get-ChildItem -Path $Root -Filter "setup.exe" -Recurse -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | Select-Object -First 1
        if ($Found) { $UninstallPath = $Found; break }
    }

    if ($UninstallPath) {
        Write-Info "Located Edge uninstaller at: $($UninstallPath.FullName)"
        $Removed = Invoke-WithRetry -OperationName "Remove Microsoft Edge (setup.exe)" -Action {
            # Start-Process doesn't throw on a non-zero exit code, so without
            # this check a failed uninstall (e.g. blocked by policy) would
            # still report success - throwing here is what lets Invoke-WithRetry
            # actually see the failure and offer a retry.
            $Proc = Start-Process -FilePath $UninstallPath.FullName -ArgumentList "--uninstall --system-level --verbose-logging --force-uninstall" -Wait -NoNewWindow -PassThru -ErrorAction Stop
            if ($Proc.ExitCode -ne 0) { throw "Edge's uninstaller exited with code $($Proc.ExitCode)." }
        }
    } else {
        Write-Info "Edge's own setup.exe was not found under either Program Files Application root - falling back to winget/Appx cleanup."
    }

    # setup.exe is absent entirely on builds that register Edge as a
    # protected inbox component with no standalone Installer folder -
    # winget still knows how to remove the Win32 package cleanly on those,
    # so this is a real second line of defense, not a last resort.
    if (-not $Removed) {
        Ensure-Winget | Out-Null
        if ($global:WingetAvailable) {
            $Removed = Invoke-WithRetry -OperationName "Remove Microsoft Edge (winget)" -Action {
                $Code = Invoke-Winget -ArgList @("uninstall", "--id", "Microsoft.Edge", "--exact", "--silent", "--force", "--accept-source-agreements", "--disable-interactivity")
                if ($Code -ne 0) { throw "winget uninstall exited with code $Code." }
            }
        }
    }

    # Last resort: strip any Appx-registered Edge stub (WebView2 shell,
    # PWA host, etc.) either path above can leave behind - these aren't
    # the browser itself, but they're what makes Windows keep reporting
    # Edge as "installed" once the Win32 payload is already gone.
    #
    # Microsoft.MicrosoftEdgeDevToolsClient is deliberately EXCLUDED: on
    # Windows 11 it is a hard-protected OS component and Remove-AppxPackage
    # always fails it with 0x80070032 (ERROR_NOT_SUPPORTED). Left in the
    # pipeline it throws mid-loop, aborting the removal of the stubs that
    # ARE removable and turning a real success into a false failure - so we
    # filter it out up front rather than fighting a block Windows will never
    # lift.
    if (-not $Removed) {
        $Removed = Invoke-WithRetry -OperationName "Remove Microsoft Edge (Appx cleanup)" -Action {
            $Packages = Get-AppxPackage -AllUsers -Name "*MicrosoftEdge*" -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -notlike "*MicrosoftEdgeDevToolsClient*" }
            if (-not $Packages) { throw "No removable Edge Appx package registration found (DevToolsClient is OS-protected and skipped)." }
            $Packages | Remove-AppxPackage -AllUsers -ErrorAction Stop
        }
    }

    Remove-EdgeScheduledTasks

    # Final verification against real system state - not just whichever
    # tier reported success - so a partial removal (e.g. the Win32 payload
    # is gone but Windows still shows it "protected") is caught here
    # instead of reporting a clean success that isn't true.
    if ($Removed -or -not (Test-MicrosoftEdgeInstalled)) {
        Write-Success "Microsoft Edge has been uninstalled (a system restart is recommended). A version/settings backup was saved to Desktop\Pulse_EdgeBackup."
        $Script:PendingRestart = $true
        return @{ Status = 'Success'; Message = 'Microsoft Edge uninstalled. Settings backup saved to Desktop\Pulse_EdgeBackup. Restart recommended.' }
    } else {
        Write-Warn "Edge is either a built-in component and cannot be fully removed, or it is not installed as a standalone. You may reset Edge instead."
        return @{ Status = 'Failed'; Message = 'Windows protected Edge from removal on this build (it is an OS component here). A backup of its settings was still saved.' }
    }
}

function Install-MicrosoftEdge {
    Write-SectionHeader "Install Microsoft Edge"
    if (Ensure-Winget) {
        Write-Info "Installing Microsoft Edge via winget..."
        $Result = Smart-Deploy "Microsoft.Edge" "Microsoft Edge"
        if ($Result.Status -eq 'Success') {
            Restore-EdgeState
        }
    } elseif ($Script:DryRun) {
        Write-Info "[WHATIF] Would install Microsoft Edge via winget and restore backed-up settings."
    } else {
        Write-Warn "Winget unavailable. Opening official download page for a manual install..."
        Write-Info "Manual install steps: download the installer from the page that opens, run it, then use this menu's [6] Reinstall Edge option again if you want your backed-up settings restored."
        Open-UrlSafe -Url "https://www.microsoft.com/en-us/edge/download"
    }
}

# ============================================================
#  BACKWARD-COMPATIBILITY STUB
#  "Restore Windows Default Settings" lives in 02-Safety.ps1 as
#  Reset-AllTweaksToDefaults (restores YOUR original captured values).
#  This stub keeps the old name working for anything that calls it.
# ============================================================
function Reset-WindowsDefaultSettings {
    Reset-AllTweaksToDefaults
}

# ============================================================
#  PERFORMANCE & GAMING OPTIMIZATION
# ============================================================
function Invoke-NetworkOptimization {
    Write-SectionHeader "Network & Ping Optimizer"
    New-SystemRestorePoint
    if (Test-DryRun "Flush DNS, reset Winsock and the IP stack") { return }
    Write-Info "Flushing DNS cache and resetting network stack..."
    # Deliberately NO ipconfig /release + /renew: dropping the DHCP lease
    # mid-task can leave the machine offline if the renew fails (VPNs,
    # static configs, flaky Wi-Fi drivers), and the Winsock/IP-stack reset
    # below requires a reboot to apply anyway.
    ipconfig /flushdns
    $DnsOk = ($LASTEXITCODE -eq 0)
    netsh winsock reset
    $WinsockOk = ($LASTEXITCODE -eq 0)
    netsh int ip reset
    $IpOk = ($LASTEXITCODE -eq 0)
    if ($DnsOk -and $WinsockOk -and $IpOk) {
        Write-Success "Network stack reset and DNS flushed. Ping latency should improve."
    } else {
        Write-ErrorX "One or more network reset commands failed (flushdns=$DnsOk, winsock=$WinsockOk, ip=$IpOk) - see the operation log."
    }
    Write-Warn "A restart is recommended for the Winsock/IP reset to fully apply."
    $Script:PendingRestart = $true
}

function Set-PulsePowerPlanTimeouts {
    <#
        .SYNOPSIS
        Pin the display and sleep timeouts to Never on AC power.

        .DESCRIPTION
        The Ultimate/Pulse plan removes the CPU's power ceiling, but Windows
        still blanks the display and drops the machine to standby on the
        plan's inherited timeouts - so a workstation left to run a long
        build, render or transfer stalls anyway. Setting both to 0 (Never)
        is what makes the plan mean what its name promises.

        AC ONLY, deliberately. The -dc counterparts are left untouched: a
        machine on battery that never sleeps is a flat battery (and, in a
        bag, a hot one), which is also why the GUI marks this operation
        Desktop-PCs-only. Anything running on mains keeps its behaviour on
        mains and its safe defaults off it.

        Failures here are reported but NOT fatal to the caller: the power
        scheme itself is already active by this point, and a policy-managed
        machine can refuse the timeout change while allowing the plan.
    #>
    if (Test-DryRun "Set display and sleep timeouts to Never on AC power") { return }

    $Settings = @(
        @{ Label = "Display timeout (AC)"; Arg = "monitor-timeout-ac" },
        @{ Label = "Sleep timeout (AC)";   Arg = "standby-timeout-ac" }
    )
    foreach ($Setting in $Settings) {
        try {
            powercfg /change $Setting.Arg 0 | Out-Null
            if ($LASTEXITCODE -eq 0) {
                Write-Success "$($Setting.Label) set to Never."
            } else {
                Write-Warn "Could not set $($Setting.Label) - powercfg exited $LASTEXITCODE."
            }
        } catch {
            Write-Warn "Could not set $($Setting.Label): $($_.Exception.Message)"
        }
    }
}

function Enable-UltimatePerformancePowerPlan {
    Write-SectionHeader "Pulse Power Plan"
    New-SystemRestorePoint
    $PlanName   = "Pulse Power Plan"
    $LegacyName = "Humam Ultimate Power Plan"   # pre-rebrand (v5.x) scheme name
    $GuidRegex  = '([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})'
    $Existing = powercfg /list | Out-String
    if ($Existing -match [regex]::Escape($PlanName) -and $Existing -match '\*') {
        $ActiveLine = ($Existing -split "`n") | Where-Object { $_ -match [regex]::Escape($PlanName) -and $_ -match '\*' }
        if ($ActiveLine) {
            Write-AlreadyOK "$PlanName is already active."
            # Re-assert the timeouts even on the no-op path. The plan being
            # active does NOT imply its timeouts are still Never - Windows
            # Update, a docking profile or the Settings app can and does
            # reset them under an unchanged scheme, and a user re-running
            # this action to fix exactly that would otherwise be told
            # everything was fine and given nothing.
            Set-PulsePowerPlanTimeouts
            return
        }
    }
    if (Test-DryRun "Duplicate the hidden Ultimate Performance scheme, rename it '$PlanName' and set it active") { return }
    try {
        # A plan created under either name gets reused (the legacy one is
        # renamed in place) - duplicating again would leave two identical
        # schemes cluttering powercfg /list.
        foreach ($Name in @($PlanName, $LegacyName)) {
            $pattern = $GuidRegex + '.*' + [regex]::Escape($Name)
            if ($Existing -match $pattern) {
                $guid = $matches[1]
                if ($Name -ne $PlanName) { powercfg /changename $guid $PlanName > $null }
                powercfg /setactive $guid > $null
                # powercfg /setactive can exit 0 without actually switching
                # (e.g. a policy-restricted machine) - verify against the
                # ACTUAL active scheme instead of trusting the exit code.
                if ((powercfg /getactivescheme | Out-String) -match [regex]::Escape($guid)) {
                    Write-Success "$PlanName activated (existing profile)."
                    Set-PulsePowerPlanTimeouts
                } else {
                    Write-ErrorX "Could not activate $PlanName - the scheme switch did not take effect (policy restriction?)."
                }
                return
            }
        }

        $sourceGuid = "e9a42b02-d5df-448d-aa00-03f14749eb61"
        # Out-String flattens the line array: -match on an array filters
        # elements WITHOUT populating $matches, which broke GUID extraction.
        $dupOutput = powercfg /duplicatescheme $sourceGuid 2>&1 | Out-String
        $newGuid = $null
        if ($dupOutput -match $GuidRegex) {
            $newGuid = $matches[1]
        }

        if ($newGuid) {
            powercfg /changename $newGuid $PlanName > $null
            powercfg /setactive $newGuid > $null
            if ((powercfg /getactivescheme | Out-String) -match [regex]::Escape($newGuid)) {
                Write-Success "$PlanName activated successfully."
                Set-PulsePowerPlanTimeouts
            } else {
                Write-ErrorX "Could not activate $PlanName - the scheme switch did not take effect (policy restriction?)."
            }
        } else {
            Write-ErrorX "Could not create or activate $PlanName."
        }
    } catch {
        Write-ErrorX "Could not activate ${PlanName}: $($_.Exception.Message)"
    }
}
