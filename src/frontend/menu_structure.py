"""
src/frontend/menu_structure.py

SINGLE SOURCE OF TRUTH for the entire GUI menu hierarchy.

Adding a new button to the app = adding ONE dict to an `items` list below.
main.py renders whatever is defined here — no UI code changes needed.

Contract with the backend (src/backend/core.ps1 + src/backend/modules/):
    Every `task` value maps 1:1 to a `switch ($TaskName)` case inside the
    Invoke-GuiTask dispatcher (src/backend/modules/30-GuiDispatcher.ps1,
    loaded by core.ps1), which must emit exactly one final
    `SUCCESS|message` or `ERROR|message` line on stdout. The GUI always
    invokes core.ps1 itself - never a module file directly.

    Tasks starting with "@" are LOCAL actions handled by the GUI itself
    (no PowerShell process is spawned):
        @open_log              -> opens Desktop\\HTCoreArchitecture_Log.txt
        @open_onedrive_backup  -> opens Desktop\\HTCore_OneDriveBackup

Item schema:
    icon     str   emoji shown on the card
    title    str   card headline
    desc     str   one-line explanation shown under the title
    task     str   core.ps1 -Task name, or "@local_action"
    timeout  int   seconds before the GUI declares a timeout   (default 300)
    confirm  bool  show a confirmation dialog before running   (default False)
    danger   bool  style the card/confirm dialog as destructive (default False)
    note     str   small badge, e.g. "Windows 11 only"          (default "")
    apps     list[tuple[str, str]]  (AppId, DisplayName) pairs - when present,
             the GUI opens the checkbox multi-selector overlay instead of a
             plain confirm dialog, and only the ticked AppIds are sent to
             core.ps1 via -AppIds. MUST mirror the corresponding $Apps_* /
             $Runtimes array in src/backend/modules/01-Catalogs.ps1 exactly
             (same IDs, same order) - the backend is the source of truth for
             what winget ID each entry installs; this list is only the GUI's
             mirror of it.
"""

# ============================================================
#  CATEGORIES  (rendered top-to-bottom in the sidebar)
# ============================================================
CATEGORIES = [
    # --------------------------------------------------------
    #  1. SOFTWARE MANAGEMENT
    # --------------------------------------------------------
    {
        "id": "software",
        "icon": "📦",
        "title": "Software Management",
        "tagline": "Deploy apps, runtimes and audit startup programs",
        "accent": "#00d4ff",
        "items": [
            {"icon": "🧰", "title": "Essential Apps",
             "desc": "Install the essential daily-driver pack (browsers, archivers, media tools) via winget.",
             "task": "InstallEssentialApps", "timeout": 3600, "confirm": True,
             "apps": [
                 ("Google.Chrome", "Google Chrome"),
                 ("Spotify.Spotify", "Spotify (Win32)"),
                 ("Discord.Discord", "Discord"),
                 ("9NKSQCEZVDDB", "WhatsApp (Store)"),
                 ("9PKTQ5699M62", "iCloud (Store)"),
                 ("Apple.iTunes", "iTunes"),
                 ("7zip.7zip", "7-Zip"),
                 ("VideoLAN.VLC", "VLC Media Player"),
             ]},
            {"icon": "👨‍💻", "title": "Programming & AI Core",
             "desc": "Developer toolchain — languages, editors and AI tooling — deployed silently.",
             "task": "InstallDevApps", "timeout": 3600, "confirm": True,
             "apps": [
                 ("Anysphere.Cursor", "Cursor IDE"),
                 ("Microsoft.VisualStudioCode", "VS Code"),
                 ("JetBrains.PyCharm.Community", "PyCharm"),
                 ("Apache.NetBeans", "NetBeans IDE"),
                 ("MSYS2.MSYS2", "GCC Compiler"),
                 ("Ollama.Ollama", "Ollama AI"),
                 ("TheDocumentFoundation.LibreOffice", "LibreOffice"),
                 ("Git.Git", "Git"),
             ]},
            {"icon": "🎮", "title": "Gaming Launchers",
             "desc": "Steam, Epic and the other launchers you actually use, in one pass.",
             "task": "InstallGamingApps", "timeout": 3600, "confirm": True,
             "apps": [
                 ("Valve.Steam", "Steam"),
                 ("EpicGames.EpicGamesLauncher", "Epic Games"),
                 ("RockstarGames.Launcher", "Rockstar Games"),
                 ("BlueStacks.BlueStacks", "BlueStacks 5"),
             ]},
            {"icon": "🔬", "title": "Hardware Diagnostics",
             "desc": "Monitoring and diagnostic utilities for CPU, GPU, RAM and disks.",
             "task": "InstallDiagnosticApps", "timeout": 3600, "confirm": True,
             "apps": [
                 ("CPUID.CPU-Z", "CPU-Z"),
                 ("TechPowerUp.GPU-Z", "GPU-Z"),
                 ("CPUID.HWMonitor", "HWMonitor"),
                 ("CrystalDewWorld.CrystalDiskInfo", "CrystalDiskInfo"),
                 ("Guru3D.Afterburner", "MSI Afterburner"),
                 ("Notion.Notion", "Notion"),
             ]},
            {"icon": "🧩", "title": "Core API Runtimes",
             "desc": "DirectX, Visual C++, .NET and Java runtimes — the bulk install.",
             "task": "InstallRuntimes", "timeout": 3600, "confirm": True,
             "apps": [
                 ("Microsoft.DirectX", "DirectX End-User Runtime"),
                 ("Microsoft.VCRedist.2015+.x64", "Visual C++ Redistributables"),
                 ("Microsoft.DotNet.DesktopRuntime.8", ".NET Desktop Runtime"),
                 ("Oracle.JavaRuntimeEnvironment", "Java Runtime Environment"),
             ]},
            {"icon": "🚀", "title": "Startup Report",
             "desc": "Audit everything that launches at boot (Run keys + Startup folders).",
             "task": "StartupReport", "timeout": 300},
            {"icon": "🧭", "title": "Verify Dev Environment",
             "desc": "PATH doctor — audit Git, Python, Java, VS Code, GCC, Node & Ollama and auto-repair missing PATH/JAVA_HOME entries.",
             "task": "VerifyEnvironment", "timeout": 300},
        ],
    },
    # --------------------------------------------------------
    #  2. SYSTEM OPTIMIZATION
    # --------------------------------------------------------
    {
        "id": "optimization",
        "icon": "⚡",
        "title": "System Optimization",
        "tagline": "Smart tweaks, performance and gaming optimizations",
        "accent": "#ffd166",
        "items": [
            {"icon": "🌙", "title": "Global Dark Mode",
             "desc": "Force the dark theme across Windows and all apps.",
             "task": "DarkMode", "timeout": 120},
            {"icon": "🖱️", "title": "Disable Mouse Acceleration",
             "desc": "True raw pointer precision — removes speed curves and thresholds.",
             "task": "DisableMouseAccel", "timeout": 120},
            {"icon": "📌", "title": "Minimalist Taskbar",
             "desc": "Left-aligned, widget-free, chat-free Windows 11 taskbar.",
             "task": "MinimalistTaskbar", "timeout": 120, "note": "Windows 11 only"},
            {"icon": "📋", "title": "Classic Context Menu",
             "desc": "Restore the full Windows 10 right-click menu — no 'Show more options'.",
             "task": "ClassicContextMenu", "timeout": 120, "note": "Windows 11 only"},
            {"icon": "🕹️", "title": "Game Mode & Game Bar",
             "desc": "Enable Game Mode and kill background recording (Game DVR).",
             "task": "GameMode", "timeout": 120},
            {"icon": "📡", "title": "Network & Ping Optimizer",
             "desc": "Flush DNS, reset Winsock and the IP stack for the lowest latency.",
             "task": "NetworkOptimization", "timeout": 300, "confirm": True},
            {"icon": "⚡", "title": "Ultimate Power Plan",
             "desc": "Unlock the hidden high-performance power scheme, renamed for you.",
             "task": "UltimatePowerPlan", "timeout": 300},
            {"icon": "☁️", "title": "Purge OneDrive",
             "desc": "Back up local OneDrive files, then terminate and uninstall OneDrive.",
             "task": "RemoveOneDrive", "timeout": 900, "confirm": True, "danger": True},
            {"icon": "🌐", "title": "Remove Microsoft Edge",
             "desc": "Uninstall Chromium Edge where Windows permits it (backup kept).",
             "task": "RemoveEdge", "timeout": 900, "confirm": True, "danger": True},
            {"icon": "🔄", "title": "Reinstall Microsoft Edge",
             "desc": "Download and install the latest Edge via winget.",
             "task": "ReinstallEdge", "timeout": 1800},
        ],
    },
    # --------------------------------------------------------
    #  3. MAINTENANCE & REPAIR
    # --------------------------------------------------------
    {
        "id": "maintenance",
        "icon": "🔧",
        "title": "Maintenance & Repair",
        "tagline": "System file repair, cache cleanup and disk optimization",
        "accent": "#64ffda",
        "items": [
            {"icon": "🛠️", "title": "System Repair (SFC + DISM)",
             "desc": "Scan and repair protected system files and the component store.",
             "task": "RunSFC", "timeout": 3600},
            {"icon": "🧹", "title": "Aggressive Cache Clean",
             "desc": "Wipe temp files, the Windows Update cache and system caches.",
             "task": "CleanCache", "timeout": 900, "confirm": True},
            {"icon": "💾", "title": "Optimize All Drives",
             "desc": "TRIM SSDs and defragment HDDs — drive by drive.",
             "task": "OptimizeDrives", "timeout": 1800},
            {"icon": "🗑️", "title": "Remove Windows.old",
             "desc": "Reclaim gigabytes held by a previous Windows installation.",
             "task": "RemoveWindowsOld", "timeout": 1800, "confirm": True, "danger": True},
            {"icon": "😴", "title": "Disable Hibernation",
             "desc": "Delete hiberfil.sys and free disk space equal to your RAM.",
             "task": "DisableHibernation", "timeout": 120},
            {"icon": "🔋", "title": "Enable Hibernation",
             "desc": "Bring hibernation (and hiberfil.sys) back.",
             "task": "EnableHibernation", "timeout": 120},
            {"icon": "📈", "title": "Drive Space Report",
             "desc": "Free / used space snapshot for every fixed drive.",
             "task": "DriveSpaceReport", "timeout": 120},
        ],
    },
    # --------------------------------------------------------
    #  4. PRIVACY & SECURITY
    # --------------------------------------------------------
    {
        "id": "privacy",
        "icon": "🛡️",
        "title": "Privacy & Security",
        "tagline": "Debloat, kill telemetry and stop data collection",
        "accent": "#f38ba8",
        "items": [
            {"icon": "📦", "title": "Remove Bloatware",
             "desc": "Uninstall the pre-loaded Store apps you never asked for.",
             "task": "RemoveBloatware", "timeout": 900, "confirm": True},
            {"icon": "🛡️", "title": "Disable Telemetry",
             "desc": "Stop diagnostic data collection services and scheduled tasks.",
             "task": "DisableTelemetry", "timeout": 300},
            {"icon": "🎯", "title": "Disable Advertising ID",
             "desc": "Remove the per-user identifier that ad networks track.",
             "task": "DisableAdvertisingID", "timeout": 120},
            {"icon": "🕓", "title": "Disable Activity History",
             "desc": "Stop Timeline activity sync to Microsoft servers.",
             "task": "DisableActivityHistory", "timeout": 120},
            {"icon": "🔒", "title": "Apply ALL Privacy Settings",
             "desc": "Run all four privacy hardening actions in a single pass.",
             "task": "ApplyAllPrivacy", "timeout": 1800, "confirm": True},
        ],
    },
    # --------------------------------------------------------
    #  5. INFORMATION & UTILITIES
    # --------------------------------------------------------
    {
        "id": "information",
        "icon": "📊",
        "title": "Information & Utilities",
        "tagline": "System insight, driver tools and the operation log",
        "accent": "#89b4fa",
        "items": [
            {"icon": "📊", "title": "System Info Snapshot",
             "desc": "Hardware summary, uptime and drive space — written to the log.",
             "task": "SystemInfo", "timeout": 300},
            {"icon": "💿", "title": "Driver Backup",
             "desc": "Export every current hardware driver to your Desktop.",
             "task": "DriverBackup", "timeout": 1800},
            {"icon": "🔍", "title": "Missing Driver Scan",
             "desc": "Query Windows Update's catalog for drivers you're missing.",
             "task": "DriverScan", "timeout": 900},
            {"icon": "🛟", "title": "Create Restore Point",
             "desc": "Manual System Restore checkpoint before big changes.",
             "task": "CreateRestorePoint", "timeout": 600},
            {"icon": "📜", "title": "View Operation Log",
             "desc": "Open the full HTCore Architecture operation log.",
             "task": "@open_log"},
        ],
    },
    # --------------------------------------------------------
    #  6. SAFETY & RECOVERY
    # --------------------------------------------------------
    {
        "id": "safety",
        "icon": "🛟",
        "title": "Safety & Recovery",
        "tagline": "Undo tweaks, restore services and recover backups",
        "accent": "#a6e3a1",
        "items": [
            {"icon": "↩️", "title": "Reset All Tweaks",
             "desc": "Revert every registry tweak to your original backed-up values.",
             "task": "ResetTweaks", "timeout": 300, "confirm": True},
            {"icon": "🔧", "title": "Restore Services",
             "desc": "Re-enable Windows services disabled by the optimizer.",
             "task": "RestoreServices", "timeout": 300},
            {"icon": "🌐", "title": "Restore Edge Backup",
             "desc": "Reinstate Microsoft Edge from the safety backup.",
             "task": "RestoreEdge", "timeout": 1800},
            {"icon": "🛟", "title": "Create Restore Point",
             "desc": "Manual System Restore checkpoint — your safety net.",
             "task": "CreateRestorePoint", "timeout": 600},
            {"icon": "☁️", "title": "OneDrive Backup Folder",
             "desc": "Open the folder holding files rescued before OneDrive removal.",
             "task": "@open_onedrive_backup"},
        ],
    },
]

# Tasks already implemented by core.ps1 v3.3's Invoke-GuiTask dispatcher.
# Everything else requires the Phase 2 backend update; until it lands, the
# GUI shows a clear "requires backend update" toast instead of a raw error.
BACKEND_V33_TASKS = frozenset({
    "DisableTelemetry", "CleanCache", "RunSFC",
    "RemoveBloatware", "OptimizeDrives", "ResetTweaks",
})


def total_operations() -> int:
    """Number of operations exposed across all categories."""
    return sum(len(c["items"]) for c in CATEGORIES)
