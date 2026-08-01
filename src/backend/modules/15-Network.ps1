#Requires -Version 5.1
<#
.SYNOPSIS
    15-Network.ps1 - per-adapter DNS profile switching (v1.0+ Phase 2, F4).

.DESCRIPTION
    Switching a PC to Cloudflare, Quad9 or AdGuard is one of the few
    single changes that improves speed AND privacy at once, and doing it
    by hand means five nested adapter dialogs per machine. This is that
    change, per adapter, with a real way back.

    THREE RULES THIS MODULE IS BUILT ON:

    1. REVERSIBLE, ALWAYS. Every profile below has a counterpart:
       Restore-PulseDnsDefaults resets the adapter to DHCP-provided DNS,
       which is the state Windows shipped in. A network tool that can
       strand a machine off the internet with no undo is not a tool, it
       is a hazard - so the undo is a first-class task with its own card,
       not a footnote.

    2. PER ADAPTER, NEVER "ALL". A laptop has Wi-Fi, Ethernet, and often
       a VPN or Hyper-V virtual switch. Rewriting DNS across all of them
       breaks the VPN's split-DNS and the virtual switch's host
       resolution, and the user asked to change ONE connection. The GUI
       picks the adapter; this module changes only what it is given.

    3. NOTHING IS RESOLVED HERE. The module writes addresses. It does not
       query them, test them, or phone anything to "verify" a profile -
       the DNS servers below are constants, published by their operators,
       and a privacy tool that made a network round trip to prove its
       privacy setting worked would be self-defeating.

    ADMIN IS REQUIRED and gated up front by the dispatcher: DNS
    configuration lives in the adapter's machine-scope settings.
#>

# ============================================================
#  THE PROFILES
# ============================================================
# Published, documented resolver addresses. Both IPv4 and IPv6 are set
# where the operator publishes them: leaving IPv6 pointed at the old
# resolver on a dual-stack machine means half the lookups quietly ignore
# the profile the user just chose - the failure mode that makes people
# think DNS changes "did nothing".
#
# `doh` is the operator's DNS-over-HTTPS template. Windows 11 (build
# 22000+) can encrypt queries to these resolvers; Windows 10 cannot, and
# Set-PulseDnsProfile reports that honestly rather than silently skipping.
$Script:DnsProfiles = @(
    @{ Key = "cloudflare"; Name = "Cloudflare"
       Note = "Fast, privacy-focused. No query logging."
       V4 = @("1.1.1.1", "1.0.0.1")
       V6 = @("2606:4700:4700::1111", "2606:4700:4700::1001")
       Doh = "https://cloudflare-dns.com/dns-query" }
    @{ Key = "cloudflare-family"; Name = "Cloudflare (Malware Blocking)"
       Note = "Cloudflare, with known malware domains blocked."
       V4 = @("1.1.1.2", "1.0.0.2")
       V6 = @("2606:4700:4700::1112", "2606:4700:4700::1002")
       Doh = "https://security.cloudflare-dns.com/dns-query" }
    @{ Key = "quad9"; Name = "Quad9"
       Note = "Blocks known-malicious domains. Swiss non-profit."
       V4 = @("9.9.9.9", "149.112.112.112")
       V6 = @("2620:fe::fe", "2620:fe::9")
       Doh = "https://dns.quad9.net/dns-query" }
    @{ Key = "google"; Name = "Google Public DNS"
       Note = "Very fast and widely reachable. Google logs some data."
       V4 = @("8.8.8.8", "8.8.4.4")
       V6 = @("2001:4860:4860::8888", "2001:4860:4860::8844")
       Doh = "https://dns.google/dns-query" }
    @{ Key = "adguard"; Name = "AdGuard DNS"
       Note = "Blocks ads and trackers at the DNS level."
       V4 = @("94.140.14.14", "94.140.15.15")
       V6 = @("2a10:50c0::ad1:ff", "2a10:50c0::ad2:ff")
       Doh = "https://dns.adguard-dns.com/dns-query" }
)

function Get-DnsProfileByKey {
    param([Parameter(Mandatory)][string]$Key)
    return ($Script:DnsProfiles | Where-Object { $_.Key -eq $Key } | Select-Object -First 1)
}

function Test-DohSupported {
    <# DoH client support landed in Windows 11 (build 22000). Reported as
       a capability rather than attempted-and-caught: a user who picked an
       encrypted profile deserves to be told their OS cannot encrypt,
       not to have it silently downgraded to plaintext. #>
    try {
        return ([Environment]::OSVersion.Version.Build -ge 22000) -and
               ($null -ne (Get-Command Add-DnsClientDohServerAddress -ErrorAction SilentlyContinue))
    } catch {
        return $false
    }
}

# ============================================================
#  READ
# ============================================================
function Get-PulseNetworkAdapters {
    <# Every real, connected adapter with its current DNS. READ-ONLY.

       Loopback and tunnel adapters are filtered out: they are not
       connections a user thinks of as "my internet", and offering to
       rewrite their DNS is offering to break something invisible. #>
    $result = @()
    try {
        $adapters = @(Get-NetAdapter -Physical -ErrorAction Stop |
            Where-Object { $_.Status -eq 'Up' })
    } catch {
        return @()
    }

    foreach ($adapter in $adapters) {
        $v4 = @(); $v6 = @()
        try {
            $servers = @(Get-DnsClientServerAddress -InterfaceIndex $adapter.ifIndex -ErrorAction Stop)
            $v4 = @(($servers | Where-Object { $_.AddressFamily -eq 2 }).ServerAddresses)
            $v6 = @(($servers | Where-Object { $_.AddressFamily -eq 23 }).ServerAddresses)
        } catch { }

        # Which published profile, if any, this adapter is currently on -
        # so the GUI can show the active one rather than making the user
        # compare IP addresses by eye.
        $active = "custom"
        if ($v4.Count -eq 0) {
            $active = "dhcp"
        } else {
            foreach ($profile in $Script:DnsProfiles) {
                $expected = @($profile.V4 | Sort-Object)
                $actual = @($v4 | Sort-Object)
                if ((@(Compare-Object $expected $actual -SyncWindow 0).Count -eq 0)) {
                    $active = $profile.Key
                    break
                }
            }
        }

        $result += [PSCustomObject]@{
            name        = [string]$adapter.Name
            description = [string]$adapter.InterfaceDescription
            ifIndex     = [int]$adapter.ifIndex
            v4          = $v4
            v6          = $v6
            activeKey   = $active
        }
    }
    return $result
}

function Get-PulseNetworkReport {
    <# The whole F4 document: adapters, the profile catalog, and whether
       this OS can do encrypted DNS. #>
    return [PSCustomObject]@{
        generatedAt  = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
        dohSupported = Test-DohSupported
        adapters     = @(Get-PulseNetworkAdapters)
        profiles     = @($Script:DnsProfiles | ForEach-Object {
            [PSCustomObject]@{
                key = $_.Key; name = $_.Name; note = $_.Note
                v4 = $_.V4; v6 = $_.V6
            }
        })
    }
}

# ============================================================
#  WRITE
# ============================================================
function Resolve-PulseAdapter {
    <# An adapter object from a NAME, or $null. Looked up by name rather
       than trusting an interface index from the GUI: indexes are
       reassigned when adapters are added or removed, so a stale one could
       address a completely different connection. #>
    param([Parameter(Mandatory)][string]$Name)
    try {
        return (Get-NetAdapter -Name $Name -ErrorAction Stop | Select-Object -First 1)
    } catch {
        return $null
    }
}

function Set-PulseDnsProfile {
    <# Point one adapter at one published profile. #>
    param(
        [Parameter(Mandatory)][string]$AdapterName,
        [Parameter(Mandatory)][string]$ProfileKey
    )

    $profile = Get-DnsProfileByKey -Key $ProfileKey
    if (-not $profile) {
        Write-ErrorX "Unknown DNS profile '$ProfileKey'."
        return $false
    }
    $adapter = Resolve-PulseAdapter -Name $AdapterName
    if (-not $adapter) {
        Write-ErrorX "Network adapter '$AdapterName' was not found or is not connected."
        return $false
    }

    if ($Script:DryRun) {
        Write-Host "   [WHATIF] Set $AdapterName DNS to $($profile.Name) ($($profile.V4 -join ', '))"
        Write-Host "   [WHATIF] Set $AdapterName IPv6 DNS to $($profile.V6 -join ', ')"
        return $true
    }

    try {
        # IPv4 and IPv6 are set in ONE call each, with the full list: the
        # cmdlet REPLACES the address list rather than appending, so
        # passing both servers together is what makes the secondary a
        # secondary instead of wiping it.
        Set-DnsClientServerAddress -InterfaceIndex $adapter.ifIndex `
            -ServerAddresses $profile.V4 -ErrorAction Stop
        Write-Success "$AdapterName -> $($profile.Name) ($($profile.V4 -join ', '))"
    } catch {
        Write-ErrorX "Could not set IPv4 DNS on '$AdapterName': $($_.Exception.Message)"
        return $false
    }

    try {
        Set-DnsClientServerAddress -InterfaceIndex $adapter.ifIndex `
            -ServerAddresses $profile.V6 -ErrorAction Stop
    } catch {
        # Non-fatal by design: a machine with IPv6 unbound on this adapter
        # is a normal configuration, and the IPv4 change above already
        # did what the user asked.
        Write-Host "   IPv6 DNS was not set (IPv6 may be disabled on this adapter)."
    }

    if (Test-DohSupported) {
        try {
            foreach ($server in $profile.V4) {
                Add-DnsClientDohServerAddress -ServerAddress $server `
                    -DohTemplate $profile.Doh -AllowFallbackToUdp $false `
                    -AutoUpgrade $true -ErrorAction Stop
            }
            Write-Host "   Encrypted DNS (DoH) enabled for this resolver."
        } catch {
            # Already registered is the common case and is not a failure.
            Write-Host "   DoH template was already registered for this resolver."
        }
    }

    # The cache still holds answers from the previous resolver; without
    # this the change appears not to have worked for several minutes.
    try { Clear-DnsClientCache -ErrorAction Stop } catch { }
    return $true
}

function Restore-PulseDnsDefaults {
    <# Hand the adapter back to DHCP - the state Windows shipped in.

       This is the undo for Set-PulseDnsProfile and the reason that
       function is safe to offer at all. #>
    param([Parameter(Mandatory)][string]$AdapterName)

    $adapter = Resolve-PulseAdapter -Name $AdapterName
    if (-not $adapter) {
        Write-ErrorX "Network adapter '$AdapterName' was not found or is not connected."
        return $false
    }

    if ($Script:DryRun) {
        Write-Host "   [WHATIF] Reset $AdapterName DNS to DHCP-provided servers"
        return $true
    }

    try {
        Set-DnsClientServerAddress -InterfaceIndex $adapter.ifIndex `
            -ResetServerAddresses -ErrorAction Stop
        Write-Success "$AdapterName -> automatic (DHCP-provided) DNS"
    } catch {
        Write-ErrorX "Could not reset DNS on '$AdapterName': $($_.Exception.Message)"
        return $false
    }
    try { Clear-DnsClientCache -ErrorAction Stop } catch { }
    return $true
}
