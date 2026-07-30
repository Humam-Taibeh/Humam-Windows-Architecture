"""
src/frontend/menu_structure.py

SINGLE SOURCE OF TRUTH for the entire GUI menu hierarchy.

Adding a new button to the app = adding ONE dict to an `items` list below.
main.py renders whatever is defined here — no UI code changes needed.

Contract with the backend (src/backend/core.ps1 + src/backend/modules/):
    Every `task` value maps 1:1 to a `switch ($TaskName)` case inside the
    Invoke-GuiTask dispatcher (src/backend/modules/30-GuiDispatcher.ps1,
    loaded by core.ps1), which must emit exactly one final
    `##PULSE##SUCCESS|message` or `##PULSE##ERROR|message` verdict line on
    stdout. The GUI always invokes core.ps1 itself - never a module file
    directly.

    Tasks starting with "@" are LOCAL actions handled by the GUI itself
    (no PowerShell process is spawned by main.py's task pipeline):
        @open_log              -> opens %LOCALAPPDATA%\\Pulse\\logs\\Pulse_Log.txt
        @open_onedrive_backup  -> opens Desktop\\Pulse_OneDriveBackup
        @playbooks             -> widgets.PlaybookDialog
        @health_report         -> widgets.HealthReportDialog
        @activation            -> widgets.ActivationStatusDialog
    The last three open a Pulse surface rather than a file. Each dialog
    runs its OWN PowerShellTask (HealthReport / ActivationStatus) rather
    than going through request_task, which is why those backend cases are
    allow-listed in tests/test_contract.py::_PROGRAMMATIC instead of being
    reachable from a card's `task`.

Item schema:
    NOTE ON `accent` (v10): a category's "accent" is a SEMANTIC MODULE KEY
    ("software", "optimization", ...), not a colour. The literal hex for
    each key lives in theme.py's per-mode token sets and is resolved at
    paint time via theme.resolve_accent(t, key), so the palette can differ
    between dark and light. It has to: the old shared hex values measured
    1.86-2.64:1 against the light-mode card, well under the 3:1 floor for
    an icon, which washed the whole colour system out in light mode.
    Widgets store the KEY and re-resolve inside apply_theme().

    icon     str   emoji shown on the card
    title    str   card headline
    desc     str   one-line explanation shown under the title
    task     str   core.ps1 -Task name, or "@local_action"
    timeout  int   seconds before the GUI declares a timeout   (default 300)
    confirm  bool  show a confirmation dialog before running   (default False)
    danger   bool  style the card/confirm dialog as destructive (default False)
    note     str   small badge, e.g. "Windows 11 only"          (default "")
    apps     list[tuple]  (AppId, DisplayName, Description, OfficialUrl)
             4-tuples - when present, the GUI opens the unified selector
             overlay (same elite row pattern as the Developer Hub: checkbox
             + per-tool "..." install-options wizard) and only the ticked
             AppIds are sent to core.ps1 via -AppIds. Description/Url are
             GUI-only metadata (tooltip + the wizard's official-site link);
             legacy 2-tuples (AppId, DisplayName) are still accepted. The
             AppId list MUST mirror the corresponding $Apps_* / $Runtimes
             array in src/backend/modules/01-Catalogs.ps1 exactly (same
             IDs, same order) - the backend is the source of truth for what
             winget ID each entry installs; this list is only the GUI's
             mirror of it.
    wizard   str   when present, the GUI opens a dedicated multi-step wizard
             dialog instead of the app selector / confirm dialog (checked
             before both). Currently only "office" -> widgets.OfficeWizardDialog,
             which resolves a setup.exe/configuration.xml pair and passes
             them to core.ps1 as -OfficeSetupPath/-OfficeConfigPath. A task
             using "wizard" should not also set "apps" or "confirm".
    devhub   bool   when True, the GUI opens widgets.DevHubSelectorDialog
             (section-grouped checkboxes, quick-select bundles, dependency
             hints, per-tool "..." install-options button) instead of the
             plain app selector. Checked before "wizard"/"apps"/"confirm".
             Sourced from DEV_HUB_GROUPS/DEV_HUB_BUNDLES below, which mirror
             $Apps_DevRuntimes/DevIDEs/DevAI/DevData/DevContainers and
             $Script:DevHubBundles/DevHubDependencyHints in 01-Catalogs.ps1.
    update_center  bool  when True, the GUI opens widgets.UpdateCenterDialog
             instead of every other selector — it runs its own live winget
             scan (task ScanForUpdates), shows a current-vs-available
             version audit, and hands back the ticked AppIds. main.py then
             runs "task" (UpdateSelectedApps) with those AppIds through the
             normal pipeline, exactly like an "apps" selection would.
    startup_manager  bool  when True, the GUI opens
             widgets.StartupManagerDialog instead of running "task"
             directly — a self-contained optimization hub (scan, group by
             recommendation, live per-item ToggleSwitch) that never hands
             anything back; main.py just opens it and moves on.
    hub      bool + items list[dict]  when True, this entry is a container,
             not a runnable action — it has no "task" and is never passed
             to core.ps1. Clicking it opens widgets.HubDialog (a single
             sub-item skips straight to that sub-item instead) rendering
             `items` as the same GlassCards a category page uses; picking
             one runs it through request_task() exactly as if it had lived
             on the page directly. This is what lets a category collapse
             to a handful of primary cards without deleting any actions —
             see CATEGORIES["software"] for the 4-hub Software Management
             layout. iter_leaf_items() below expands every hub so leaf
             actions stay reachable from the Ctrl+K command palette.
             A hub may instead supply `groups` (list of
             {"title": str, "items": list[dict]}) in place of a flat
             `items` list: the HubDialog then renders each group's title as
             a small "section" header above its cards, so a hub with many
             sub-actions stays tidy and scannable (System Tools &
             Utilities uses this). hub_items() flattens either shape, so
             counters, the command palette and hub navigation treat grouped
             and flat hubs identically.
"""

# ============================================================
#  DEVELOPER & UNIVERSITY HUB DATA
#  Mirrors 01-Catalogs.ps1's $Apps_DevRuntimes / DevIDEs / DevAI / DevData /
#  DevContainers (same IDs, same order, group-for-group) plus
#  $Script:DevHubBundles / DevHubDependencyHints. The backend is still the
#  source of truth for what winget ID each entry installs and which task
#  name runs the bulk deploy (InstallDevHub) - this is the GUI's mirror,
#  extended with the description/URL/dependency-hint metadata the richer
#  DevHubSelectorDialog needs that a plain (AppId, DisplayName) pair can't
#  carry.
#
#  Each tool entry: (AppId, DisplayName, WhyYouNeedIt, OfficialUrl,
#                     RequiresAppId | None, RequiresDisplayName | None)
# ============================================================
DEV_HUB_GROUPS = [
    ("🧩 Core Runtimes & Compilers", [
        ("Python.Python.3.12", "Python 3.12",
         "General-purpose language for scripting, data science and AI/ML projects.",
         "https://www.python.org/downloads/", None, None),
        ("EclipseAdoptium.Temurin.21.JDK", "Java JDK (Temurin 21)",
         "The Java Development Kit — compiles and runs Java projects; NetBeans and IntelliJ both need this.",
         "https://adoptium.net/temurin/releases/", None, None),
        ("OpenJS.NodeJS.LTS", "Node.js (LTS)",
         "JavaScript runtime for web backends, build tools and npm packages.",
         "https://nodejs.org/en/download", None, None),
        ("Git.Git", "Git / Git Bash",
         "Version control — track changes and collaborate on any codebase.",
         "https://git-scm.com/downloads", None, None),
        ("MSYS2.MSYS2", "GCC / MinGW-w64 Compiler",
         "C/C++ compiler toolchain for native Windows builds.",
         "https://www.msys2.org/", None, None),
    ]),
    ("🛠️ IDEs & Editors", [
        ("Microsoft.VisualStudioCode", "VS Code",
         "Lightweight, extensible code editor — the daily driver for most languages.",
         "https://code.visualstudio.com/download", None, None),
        ("Anysphere.Cursor", "Cursor IDE",
         "AI-native code editor built on VS Code, with built-in AI pair programming.",
         "https://cursor.sh/", None, None),
        ("JetBrains.PyCharm.Community", "PyCharm Community",
         "Full-featured Python IDE with debugging, refactoring and test tools.",
         "https://www.jetbrains.com/pycharm/download/",
         "Python.Python.3.12", "Python 3.12"),
        ("JetBrains.IntelliJIDEA.Community", "IntelliJ IDEA Community",
         "Full-featured Java IDE with deep code intelligence and refactoring.",
         "https://www.jetbrains.com/idea/download/",
         "EclipseAdoptium.Temurin.21.JDK", "Java JDK"),
        ("Apache.NetBeans", "NetBeans IDE",
         "Java IDE popular in university courses — project templates and a visual GUI builder.",
         "https://netbeans.apache.org/download/index.html",
         "EclipseAdoptium.Temurin.21.JDK", "Java JDK"),
    ]),
    ("🧠 AI & Local LLM Stack", [
        ("Ollama.Ollama", "Ollama (Local LLM Runner)",
         "Run open-source LLMs (Llama, Mistral, etc.) locally — no cloud required.",
         "https://ollama.com/download", None, None),
        ("OpenWebUI.OpenWebUI", "Open WebUI (Local Chat Interface)",
         "A ChatGPT-style web interface for models running in Ollama.",
         "https://openwebui.com/", None, None),
    ]),
    ("🗄️ Databases & API Tools", [
        ("DBeaver.DBeaver.Community", "DBeaver (Database Client)",
         "Universal SQL client — browse and query almost any database.",
         "https://dbeaver.io/download/", None, None),
        ("Postman.Postman", "Postman (API Client)",
         "Build, test and document REST/GraphQL APIs.",
         "https://www.postman.com/downloads/", None, None),
        ("Bruno.Bruno", "Bruno (Open-Source API Client)",
         "A fast, open-source Postman alternative that stores collections as local files.",
         "https://www.usebruno.com/downloads", None, None),
    ]),
    ("🐳 Containerization", [
        ("Docker.DockerDesktop", "Docker Desktop",
         "Build and run containers — package an app with everything it needs to run anywhere.",
         "https://www.docker.com/products/docker-desktop/", None, None),
    ]),
]

DEV_HUB_BUNDLES = [
    {"key": "java-university", "icon": "🎓", "title": "Java / University Stack",
     "app_ids": ["EclipseAdoptium.Temurin.21.JDK", "Apache.NetBeans",
                 "JetBrains.IntelliJIDEA.Community", "Git.Git", "Microsoft.VisualStudioCode"]},
    {"key": "ai-python", "icon": "🧠", "title": "AI / Python Stack",
     "app_ids": ["Python.Python.3.12", "Ollama.Ollama", "OpenWebUI.OpenWebUI", "Microsoft.VisualStudioCode"]},
    {"key": "web-dev", "icon": "🌐", "title": "Web Dev Stack",
     "app_ids": ["OpenJS.NodeJS.LTS", "Git.Git", "Microsoft.VisualStudioCode", "Postman.Postman"]},
]

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
        "glyph": "package",
        "title": "Software Management",
        "tagline": "Deploy apps, runtimes and audit startup programs",
        "accent": "software",
        "items": [
            # -- HUB 1: everyday consumer/productivity apps ---------------
            {"icon": "🧰", "glyph": "globe", "title": "Browsers & Daily Apps",
             "desc": "Browsers, chat, media and productivity essentials.",
             "hub": True,
             "items": [
                 {"icon": "🌐", "title": "Browsers, Chat & Media",
                  "desc": "Chrome, Brave, Discord, Spotify, VLC, 7-Zip and more.",
                  "glyph": "globe", "task": "InstallEssentialApps", "timeout": 3600, "confirm": True,
                  "apps": [
                      ("Google.Chrome", "Google Chrome",
                       "Fast, secure web browser from Google.",
                       "https://www.google.com/chrome/"),
                      ("Brave.Brave", "Brave Browser",
                       "Privacy-first Chromium browser with built-in ad blocking.",
                       "https://brave.com/download/"),
                      ("Mozilla.Firefox", "Mozilla Firefox",
                       "Fast, independent browser built on open standards.",
                       "https://www.mozilla.org/firefox/new/"),
                      ("Microsoft.Edge", "Microsoft Edge",
                       "Microsoft's Chromium browser — reinstalls cleanly here even after using Remove Microsoft Edge.",
                       "https://www.microsoft.com/en-us/edge/download"),
                      ("Telegram.TelegramDesktop", "Telegram Desktop",
                       "Fast, secure cloud-based messaging.",
                       "https://telegram.org/apps"),
                      ("Spotify.Spotify", "Spotify (Win32)",
                       "Music and podcast streaming client.",
                       "https://www.spotify.com/download/windows/"),
                      ("Discord.Discord", "Discord",
                       "Voice, video and text chat for friends and communities.",
                       "https://discord.com/download"),
                      ("9NKSQCEZVDDB", "WhatsApp (Store)",
                       "Official WhatsApp messenger for the desktop.",
                       "https://www.whatsapp.com/download"),
                      ("9PKTQ5699M62", "iCloud (Store)",
                       "Access iCloud Photos, Drive and Passwords on Windows.",
                       "https://www.apple.com/icloud/"),
                      ("Apple.iTunes", "iTunes",
                       "Media library and Apple device sync.",
                       "https://www.apple.com/itunes/"),
                      ("7zip.7zip", "7-Zip",
                       "Open-source archiver with best-in-class compression.",
                       "https://www.7-zip.org/"),
                      ("VideoLAN.VLC", "VLC Media Player",
                       "Plays practically every audio and video format ever made.",
                       "https://www.videolan.org/vlc/"),
                      ("TheDocumentFoundation.LibreOffice", "LibreOffice",
                       "Free office suite — Writer, Calc, Impress and more.",
                       "https://www.libreoffice.org/download/download-libreoffice/"),
                      ("Notion.Notion", "Notion",
                       "All-in-one notes, docs and project workspace.",
                       "https://www.notion.com/desktop"),
                  ]},
                 {"icon": "📄", "title": "Microsoft Office Suite",
                  "desc": "Word, Excel, PowerPoint and Outlook via the official ODT.",
                  "glyph": "document", "task": "InstallOfficeODT", "timeout": 3600, "wizard": "office"},
                 # OneDrive install/restore was moved OUT of here to live
                 # beside 'Purge OneDrive' under System Tools & Utilities (all
                 # OneDrive tools in one place); Microsoft Teams was dropped
                 # from the catalog entirely. This hub is now exactly three
                 # core app selections: apps, Office and runtimes.
                 {"icon": "🧩", "title": "Core API Runtimes",
                  "desc": "DirectX, Visual C++, .NET and Java prerequisites.",
                  "glyph": "puzzle", "task": "InstallRuntimes", "timeout": 3600, "confirm": True,
                  "apps": [
                      ("Microsoft.DirectX", "DirectX End-User Runtime",
                       "Legacy DirectX libraries that older games still need.",
                       "https://www.microsoft.com/en-us/download/details.aspx?id=35"),
                      ("Microsoft.VCRedist.2015+.x64", "Visual C++ Redistributables",
                       "C++ runtime DLLs required by countless Windows apps.",
                       "https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist"),
                      ("Microsoft.DotNet.DesktopRuntime.8", ".NET Desktop Runtime",
                       "Runs modern .NET desktop applications.",
                       "https://dotnet.microsoft.com/en-us/download/dotnet/8.0"),
                      ("Oracle.JavaRuntimeEnvironment", "Java Runtime Environment",
                       "Runs Java desktop applications.",
                       "https://www.java.com/en/download/"),
                  ]},
             ]},
            # -- HUB 2: developer tooling (single flow -> direct passthrough) --
            {"icon": "🎓", "glyph": "code", "title": "Developer & University Hub",
             "desc": "VS Code, Python, Java, Git, Node.js and more.",
             "hub": True,
             "items": [
                 {"icon": "🎓", "title": "Developer & University Hub",
                  "desc": "Runtimes, IDEs, AI tools, databases and containers.",
                  "glyph": "code", "task": "InstallDevHub", "timeout": 3600, "devhub": True},
             ]},
            # -- HUB 3: gaming (single flow -> direct passthrough) --------
            {"icon": "🎮", "glyph": "game", "title": "Gaming & Launchers",
             "desc": "Steam, Epic, Rockstar and BlueStacks.",
             "hub": True,
             "items": [
                 {"icon": "🎮", "title": "Gaming Launchers",
                  "desc": "Steam, Epic and more — GPU software added automatically.",
                  "glyph": "game", "task": "InstallGamingApps", "timeout": 3600, "confirm": True,
                  "apps": [
                      ("Valve.Steam", "Steam",
                       "The largest PC game store and launcher.",
                       "https://store.steampowered.com/about/"),
                      ("EpicGames.EpicGamesLauncher", "Epic Games",
                       "Epic's store and launcher — free weekly games included.",
                       "https://store.epicgames.com/en-US/download"),
                      ("RockstarGames.Launcher", "Rockstar Games",
                       "Rockstar's launcher for GTA, Red Dead and more.",
                       "https://socialclub.rockstargames.com/rockstar-games-launcher"),
                      ("BlueStacks.BlueStacks", "BlueStacks 5",
                       "Android app player — run mobile games on Windows.",
                       "https://www.bluestacks.com/download.html"),
                  ]},
             ]},
            # -- HUB 4: diagnostics, environment repair, optimization -----
            #
            # Grouped, not flat: eight sub-actions read as clutter in one
            # undifferentiated list, so they're split into three scannable
            # sub-groups the HubDialog renders under small "section" headers
            # — Diagnostics & Optimization (the always-useful utilities),
            # then the Edge and OneDrive remove/restore pairs kept together
            # so each app's teardown and its counterpart restore sit side by
            # side. `groups` is the grouped analogue of a hub's flat `items`;
            # menu_structure's hub_items() flattens it for the command
            # palette / counters and main.py's hub navigation.
            {"icon": "🛠️", "glyph": "tools", "title": "System Tools & Utilities",
             "desc": "Diagnostics, environment repair and update audits.",
             "hub": True,
             "groups": [
                 {"title": "DIAGNOSTICS & OPTIMIZATION", "items": [
                     {"icon": "🔬", "title": "Hardware Diagnostics",
                      "desc": "Monitors CPU, GPU, RAM and disk health.",
                      "glyph": "diagnostics", "task": "InstallDiagnosticApps", "timeout": 3600, "confirm": True,
                      "apps": [
                          ("CPUID.CPU-Z", "CPU-Z",
                           "CPU, motherboard and memory identification tool.",
                           "https://www.cpuid.com/softwares/cpu-z.html"),
                          ("TechPowerUp.GPU-Z", "GPU-Z",
                           "Graphics card information, sensors and BIOS tools.",
                           "https://www.techpowerup.com/gpuz/"),
                          ("CPUID.HWMonitor", "HWMonitor",
                           "Live voltages, temperatures and fan speeds.",
                           "https://www.cpuid.com/softwares/hwmonitor.html"),
                          ("CrystalDewWorld.CrystalDiskInfo", "CrystalDiskInfo",
                           "Drive health and S.M.A.R.T. monitoring.",
                           "https://crystalmark.info/en/software/crystaldiskinfo/"),
                          ("Guru3D.Afterburner", "MSI Afterburner",
                           "GPU overclocking and on-screen performance monitoring.",
                           "https://www.msi.com/Landing/afterburner"),
                      ]},
                     {"icon": "🧭", "title": "PATH Doctor",
                      "desc": "Makes Windows find your dev tools by name in any terminal.",
                      "glyph": "terminal", "task": "VerifyEnvironment", "timeout": 300},
                     {"icon": "🚀", "title": "Startup Manager",
                      "desc": "Boot-impact audit with instant enable/disable toggles.",
                      "glyph": "boot", "task": "StartupReport", "timeout": 300, "startup_manager": True},
                     {"icon": "🔄", "title": "Check for Updates",
                      "desc": "Live scan of installed apps — update exactly what you pick.",
                      "glyph": "refresh", "task": "UpdateSelectedApps", "timeout": 3600, "update_center": True},
                 ]},
                 {"title": "MICROSOFT EDGE", "items": [
                     {"icon": "🌐", "title": "Remove Microsoft Edge",
                      "desc": "Force-purge Chromium Edge, with a backup kept.",
                      "glyph": "delete", "task": "RemoveEdge", "timeout": 900, "confirm": True, "danger": True},
                     {"icon": "🔁", "title": "Reinstall Microsoft Edge",
                      "desc": "Reinstall Edge and restore your backed-up settings.",
                      "glyph": "sync", "task": "RestoreEdge", "timeout": 1800},
                 ]},
                 {"title": "MICROSOFT ONEDRIVE", "items": [
                     {"icon": "☁️", "title": "Purge OneDrive",
                      "desc": "Back up local files, then uninstall OneDrive.",
                      "glyph": "cloud", "task": "RemoveOneDrive", "timeout": 900, "confirm": True, "danger": True},
                     {"icon": "🔁", "title": "Install / Restore OneDrive",
                      "desc": "Reinstall OneDrive so it's back and syncing.",
                      "glyph": "sync", "task": "RestoreOneDrive", "timeout": 1800},
                 ]},
             ]},
        ],
    },
    # --------------------------------------------------------
    #  2. SYSTEM OPTIMIZATION
    # --------------------------------------------------------
    {
        "id": "optimization",
        "icon": "⚡",
        "glyph": "bolt",
        "title": "System Optimization",
        "tagline": "Smart tweaks, performance and gaming optimizations",
        "accent": "optimization",
        "items": [
            {"icon": "🌙", "title": "Global Dark Mode",
             "desc": "Force the dark theme across Windows and all apps.",
             "glyph": "moon", "task": "DarkMode", "timeout": 120},
            {"icon": "🖱️", "title": "Disable Mouse Acceleration",
             "desc": "Raw pointer precision — no speed curves or thresholds.",
             "glyph": "mouse", "task": "DisableMouseAccel", "timeout": 120},
            {"icon": "📌", "title": "Minimalist Taskbar",
             "desc": "Left-aligned, widget-free, chat-free taskbar.",
             "glyph": "pin", "task": "MinimalistTaskbar", "timeout": 120, "note": "Windows 11 only"},
            {"icon": "📋", "title": "Classic Context Menu",
             "desc": "Restore the full Windows 10 right-click menu.",
             "glyph": "list", "task": "ClassicContextMenu", "timeout": 120, "note": "Windows 11 only"},
            {"icon": "🕹️", "title": "Game Mode & Game Bar",
             "desc": "Enable Game Mode, kill background recording.",
             "glyph": "game", "task": "GameMode", "timeout": 120},
            {"icon": "📡", "title": "Network & Ping Optimizer",
             "desc": "Flush DNS and reset Winsock for lower latency.",
             "glyph": "network", "task": "NetworkOptimization", "timeout": 300, "confirm": True},
            {"icon": "⚡", "title": "Ultimate Power Plan",
             "desc": "Unlock the hidden high-performance power scheme.",
             "glyph": "bolt", "task": "UltimatePowerPlan", "timeout": 300},
        ],
    },
    # --------------------------------------------------------
    #  3. MAINTENANCE & REPAIR
    # --------------------------------------------------------
    {
        "id": "maintenance",
        "icon": "🔧",
        "glyph": "repair",
        "title": "Maintenance & Repair",
        "tagline": "System file repair, cache cleanup and disk optimization",
        "accent": "maintenance",
        "items": [
            {"icon": "🛠️", "title": "System Repair (SFC + DISM)",
             "desc": "Repair protected system files and the component store.",
             "glyph": "repair", "task": "RunSFC", "timeout": 3600},
            {"icon": "🧹", "title": "Aggressive Cache Clean",
             "desc": "Wipe temp, Windows Update and system caches.",
             "glyph": "broom", "task": "CleanCache", "timeout": 900, "confirm": True},
            {"icon": "💾", "title": "Optimize All Drives",
             "desc": "TRIM SSDs and defragment HDDs — drive by drive.",
             "glyph": "disk", "task": "OptimizeDrives", "timeout": 1800},
            {"icon": "🗑️", "title": "Remove Windows.old",
             "desc": "Reclaim gigabytes from a previous Windows install.",
             "glyph": "delete", "task": "RemoveWindowsOld", "timeout": 1800, "confirm": True, "danger": True},
            {"icon": "😴", "title": "Disable Hibernation",
             "desc": "Delete hiberfil.sys and free disk space.",
             "glyph": "sleep", "task": "DisableHibernation", "timeout": 120},
            {"icon": "🔋", "title": "Enable Hibernation",
             "desc": "Bring hibernation (and hiberfil.sys) back.",
             "glyph": "battery", "task": "EnableHibernation", "timeout": 120},
            {"icon": "📈", "title": "Drive Space Report",
             "desc": "Free / used space snapshot for every fixed drive.",
             "glyph": "chart", "task": "DriveSpaceReport", "timeout": 120},
        ],
    },
    # --------------------------------------------------------
    #  4. PRIVACY & SECURITY
    # --------------------------------------------------------
    {
        "id": "privacy",
        "icon": "🛡️",
        "glyph": "shield",
        "title": "Privacy & Security",
        "tagline": "Debloat, kill telemetry and stop data collection",
        "accent": "privacy",
        "items": [
            {"icon": "📦", "title": "Remove Bloatware",
             "desc": "Uninstall pre-loaded Store apps you never asked for.",
             "glyph": "delete", "task": "RemoveBloatware", "timeout": 900, "confirm": True},
            {"icon": "🛡️", "title": "Disable Telemetry",
             "desc": "Stop diagnostic data collection and scheduled tasks.",
             "glyph": "shieldplain", "task": "DisableTelemetry", "timeout": 300},
            {"icon": "🎯", "title": "Disable Advertising ID",
             "desc": "Remove the per-user identifier that ad networks track.",
             "glyph": "target", "task": "DisableAdvertisingID", "timeout": 120},
            {"icon": "🕓", "title": "Disable Activity History",
             "desc": "Stop Timeline activity sync to Microsoft servers.",
             "glyph": "history", "task": "DisableActivityHistory", "timeout": 120},
            {"icon": "🔒", "title": "Apply ALL Privacy Settings",
             "desc": "Run every privacy hardening action in one pass.",
             "glyph": "defender", "task": "ApplyAllPrivacy", "timeout": 1800, "confirm": True},
        ],
    },
    # --------------------------------------------------------
    #  5. INFORMATION & UTILITIES
    # --------------------------------------------------------
    {
        "id": "information",
        "icon": "📊",
        "glyph": "info",
        "title": "Information & Utilities",
        "tagline": "System insight, driver tools and the operation log",
        "accent": "information",
        "items": [
            {"icon": "📊", "title": "System Info Snapshot",
             "desc": "Hardware, uptime and drive space — written to the log.",
             "glyph": "chartline", "task": "SystemInfo", "timeout": 300},
            {"icon": "💿", "title": "Driver Backup",
             "desc": "Export every current hardware driver to your Desktop.",
             "glyph": "save", "task": "DriverBackup", "timeout": 1800},
            {"icon": "🔍", "title": "Missing Driver Scan",
             "desc": "Check Windows Update for drivers you're missing.",
             "glyph": "search", "task": "DriverScan", "timeout": 900},
            {"icon": "🛟", "title": "Create Restore Point",
             "desc": "Manual System Restore checkpoint before big changes.",
             "glyph": "restorepoint", "task": "CreateRestorePoint", "timeout": 600},
            {"icon": "📜", "title": "View Operation Log",
             "desc": "Open the full Pulse operation log.",
             "glyph": "log", "task": "@open_log"},
        ],
    },
    # --------------------------------------------------------
    #  6. SAFETY & RECOVERY
    # --------------------------------------------------------
    {
        "id": "safety",
        "icon": "🛟",
        "glyph": "restore",
        "title": "Safety & Recovery",
        "tagline": "Undo tweaks, restore services and recover backups",
        "accent": "safety",
        "items": [
            {"icon": "↩️", "title": "Reset All Tweaks",
             "desc": "Revert every registry tweak to your backed-up values.",
             "glyph": "restore", "task": "ResetTweaks", "timeout": 300, "confirm": True},
            {"icon": "🔧", "title": "Restore Services",
             "desc": "Re-enable Windows services disabled by the optimizer.",
             "glyph": "repair", "task": "RestoreServices", "timeout": 300},
            {"icon": "🛟", "title": "Create Restore Point",
             "desc": "Manual System Restore checkpoint — your safety net.",
             "glyph": "restorepoint", "task": "CreateRestorePoint", "timeout": 600},
            # Read-only, and deliberately so: it reports what Windows'
            # licensing service already knows and hands off to Settings for
            # anything that needs changing. Nothing in Pulse activates,
            # keys, or alters a licence.
            {"icon": "🔑", "title": "Activation Status",
             "desc": "Windows and Office licence state, channel and expiry — read-only.",
             "glyph": "key", "task": "@activation"},
            {"icon": "☁️", "title": "OneDrive Backup Folder",
             "desc": "Open files rescued before OneDrive removal.",
             "glyph": "folder", "task": "@open_onedrive_backup"},
        ],
    },
    # --------------------------------------------------------
    #  7. AUTOMATION  (v10.3)
    #
    #  Both entries are GUI-LOCAL ("@") on purpose. A playbook is an
    #  ordered list of tasks the dispatcher already has cases for, and the
    #  health report is assembled by the GUI from probes that already
    #  exist — neither adds a backend case, which is why neither appears
    #  in 30-GuiDispatcher.ps1 and why test_contract's reachability check
    #  still balances.
    # --------------------------------------------------------
    {
        "id": "automation",
        "icon": "📘",
        "glyph": "boot",
        "title": "Automation",
        "tagline": "Repeatable playbooks and a system health report",
        "accent": "automation",
        "items": [
            {"icon": "📘", "title": "Playbooks",
             "desc": "Run a saved sequence of tasks — preview it first with a dry run.",
             "glyph": "boot", "task": "@playbooks"},
            {"icon": "🩺", "title": "Health & Drift Report",
             "desc": "Snapshot applied tweaks, drives and startup load; export HTML or JSON.",
             "glyph": "chart", "task": "@health_report"},
        ],
    },
]

def hub_items(hub: dict) -> list[dict]:
    """Flat list of a hub's runnable sub-actions, regardless of whether the
    hub stores them directly under `items` or split across titled `groups`
    (the System Tools & Utilities hub uses `groups` so the HubDialog can
    render them under section headers). Every consumer that needs the leaf
    actions — the command palette, the operation counter, hub navigation —
    goes through here so grouped and flat hubs behave identically."""
    if hub.get("groups"):
        return [sub for group in hub["groups"] for sub in group["items"]]
    return hub.get("items", [])


def _count_leaves(items: list[dict]) -> int:
    return sum(
        _count_leaves(hub_items(it)) if it.get("hub") else 1
        for it in items
    )


def find_action(cat_index: int, task: str) -> tuple[dict | None, str]:
    """(item, category_accent_KEY) for a runnable action located by its `task`
    name within CATEGORIES[cat_index], expanding hub containers recursively.
    Powers the Welcome dashboard's Quick Actions band — direct shortcuts to
    the highest-value single operations, deliberately DISTINCT from the
    sidebar's module navigation (which the dashboard no longer duplicates).
    Returns (None, accent) when the task can't be found, so a renamed or
    removed task degrades gracefully instead of crashing the landing
    screen."""
    cat = CATEGORIES[cat_index]

    def walk(items: list[dict]) -> dict | None:
        for it in items:
            if it.get("hub"):
                found = walk(hub_items(it))
                if found is not None:
                    return found
            elif it.get("task") == task:
                return it
        return None

    return walk(cat["items"]), cat["accent"]


# ============================================================
#  ADMIN-GATED TASKS — GUI mirror of the backend gate
# ============================================================
# MUST stay in sync with $Script:AdminRequiredTasks in
# src/backend/modules/01-Catalogs.ps1 — the backend is the authority (it
# still rejects an admin task with a clean ERROR verdict even if this drifts),
# but the GUI mirrors the list so it can PRE-CHECK before spawning PowerShell:
# a non-elevated admin action shows an inline "relaunch elevated" prompt
# instead of a spawn-then-fail round trip, and admin-gated Quick Action cards
# can show a lock affordance up front. An automated equality check
# (tests/scratchpad) guards the two lists against drift.
#
# Software install/update tasks are deliberately absent for the same reason
# the backend omits them: winget + each installer handle their own elevation,
# and blanket-requiring admin breaks user-scope / elevation-prohibited
# packages (e.g. Spotify). See the 01-Catalogs.ps1 comment for the full why.
ADMIN_REQUIRED_TASKS = frozenset({
    "RunSFC", "CleanCache", "RemoveBloatware", "OptimizeDrives", "RemoveWindowsOld",
    "DisableHibernation", "EnableHibernation", "DisableTelemetry", "DisableActivityHistory",
    "NetworkOptimization", "UltimatePowerPlan", "RemoveOneDrive", "RemoveEdge",
    "CreateRestorePoint", "DriverBackup", "RestoreServices", "RestoreEdge", "RestoreOneDrive",
    "ApplyAllPrivacy", "ResetTweaks", "InstallOfficeODT", "InstallOfficeODTAuto",
    "StartupDisableItem", "StartupEnableItem",
})


def requires_admin(task: str | None) -> bool:
    """True if `task` writes HKLM / services / machine state and therefore
    needs an elevated Pulse. Used by the GUI to pre-check before running an
    admin-gated action (see main.PulseApp.request_task)."""
    return bool(task) and task in ADMIN_REQUIRED_TASKS


def _count_leaves(items: list[dict]) -> int:
    return sum(_count_leaves(hub_items(it)) if it.get("hub") else 1
               for it in items)


def category_operations(category: dict) -> int:
    """Runnable operations inside one category, counting THROUGH hub
    containers — a hub card is a container, not an operation, so a naive
    len(category["items"]) under-reports Software Management by a factor
    of three. Powers the category header's count chip."""
    return _count_leaves(category["items"])


def search_haystack(item: dict) -> str:
    """Everything a category-page filter should match an item against,
    lowercased. Hub containers fold in their sub-items' titles so typing
    "office" still surfaces the Browsers & Daily Apps hub that holds it —
    otherwise filtering would hide actions that are genuinely there, just
    one level down."""
    parts = [item.get("title", ""), item.get("desc", ""), item.get("note", "")]
    if item.get("hub"):
        parts.extend(sub.get("title", "") for sub in hub_items(item))
    return " ".join(parts).lower()


def accent_for_task(task: str | None) -> str:
    """The module accent KEY of whichever category owns `task` (hubs
    expanded), or "" when nothing matches. Lets a consumer that only has a
    task name — the Recent Operations trail, which is re-read from storage
    with no widget context — colour it in its own module's accent."""
    if not task:
        return ""
    for cat in CATEGORIES:
        def walk(items: list[dict]) -> bool:
            for it in items:
                if it.get("hub"):
                    if walk(hub_items(it)):
                        return True
                elif it.get("task") == task:
                    return True
            return False

        if walk(cat["items"]):
            return cat["accent"]
    return ""


def iter_leaf_items():
    """Yields (item, breadcrumb) for every runnable action, expanding hub
    containers — used by the Ctrl+K command palette so a hub's sub-actions
    (e.g. 'Microsoft Office Suite', tucked inside the Browsers & Daily Apps
    hub) stay searchable even though the category page now shows only the
    hub card itself."""
    for cat in CATEGORIES:
        for item in cat["items"]:
            if item.get("hub"):
                for sub in hub_items(item):
                    yield sub, f"{cat['title']} › {item['title']}"
            else:
                yield item, cat["title"]
