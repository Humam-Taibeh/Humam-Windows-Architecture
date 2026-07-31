#!/usr/bin/env python3
"""
tools/fetch_app_icons.py

BUILD-TIME asset fetcher for Software Management's brand logos.

Run this once (or when the catalog changes) to populate
assets/appicons/. Pulse itself NEVER touches the network: the app reads
only the files this script leaves behind, which is what keeps an
elevated, privacy-focused Windows utility from phoning out to draw its
own UI. See src/utils/appicons.py for the runtime half.

    python tools/fetch_app_icons.py            # fetch missing only
    python tools/fetch_app_icons.py --force    # re-fetch everything

SOURCE: Simple Icons (https://simpleicons.org), the standard brand-mark
set for exactly this problem. The SVG files are CC0; the marks themselves
remain their owners' trademarks and are used here nominatively — to
identify the software a row installs, which is the same basis every
package manager and app store relies on.

WHY A HAND-WRITTEN MAP AND NOT FUZZY MATCHING: a wrong logo is worse than
no logo. "Cursor" fuzzy-matches a mouse-cursor icon; "MSI" matches both
the hardware brand and the installer format. Every pairing below was
checked by eye against the Simple Icons index, and anything without an
authentic mark is mapped to None rather than to something that merely
looks close.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSET_DIR = os.path.join(ROOT, "assets", "appicons")
MANIFEST = os.path.join(ASSET_DIR, "manifest.json")
INDEX_URL = "https://cdn.jsdelivr.net/npm/simple-icons@13/_data/simple-icons.json"
ICON_URL = "https://cdn.jsdelivr.net/npm/simple-icons@13/icons/{slug}.svg"

#: winget AppId -> Simple Icons slug, or None when no authentic mark is
#: available. The None entries are documented, not forgotten:
#:
#:   Microsoft.VisualStudioCode / Microsoft.Edge / Microsoft.DirectX
#:       Simple Icons REMOVED every Microsoft product mark under
#:       Microsoft's trademark policy. Substituting VSCodium's logo for VS
#:       Code (a different product) or a generic Microsoft mark would be
#:       inaccurate, so these fall through to the runtime's other sources.
#:       In practice they usually resolve anyway: Edge ships with Windows
#:       and VS Code is typically installed, so appicons.py reads their
#:       real icon straight out of the installed binary.
#:   Anysphere.Cursor / BlueStacks.BlueStacks / OpenWebUI.OpenWebUI
#:       Not in the Simple Icons set at all.
#:   CPUID.* / CrystalDewWorld.* / TechPowerUp.GPU-Z
#:       Small hardware utilities with no published brand mark in any
#:       open set.
#:
#: Drop an <AppId>.svg into assets/appicons/ by hand to cover any of these
#: — the loader prefers a file on disk over everything except a locally
#: installed app's own icon.
ICON_MAP: dict[str, str | None] = {
    # -- browsers, chat, media, productivity ------------------------
    "Google.Chrome": "googlechrome",
    "Brave.Brave": "brave",
    "Mozilla.Firefox": "firefoxbrowser",
    "Microsoft.Edge": None,
    "Telegram.TelegramDesktop": "telegram",
    "Spotify.Spotify": "spotify",
    "Discord.Discord": "discord",
    "9NKSQCEZVDDB": "whatsapp",
    "9PKTQ5699M62": "icloud",
    "Apple.iTunes": "itunes",
    "7zip.7zip": "7zip",
    "VideoLAN.VLC": "vlcmediaplayer",
    "TheDocumentFoundation.LibreOffice": "libreoffice",
    "Notion.Notion": "notion",
    # -- runtimes ---------------------------------------------------
    "Microsoft.DirectX": None,
    # the C++ language mark, not a Microsoft one — accurate for a C++
    # redistributable and unencumbered
    "Microsoft.VCRedist.2015+.x64": "cplusplus",
    "Microsoft.DotNet.DesktopRuntime.8": "dotnet",
    # Oracle's own mark: this entry is Oracle's JRE, NOT OpenJDK
    "Oracle.JavaRuntimeEnvironment": "oracle",
    # -- gaming -----------------------------------------------------
    "Valve.Steam": "steam",
    "EpicGames.EpicGamesLauncher": "epicgames",
    "RockstarGames.Launcher": "rockstargames",
    "BlueStacks.BlueStacks": None,
    # -- diagnostics ------------------------------------------------
    "CPUID.CPU-Z": None,
    "CPUID.HWMonitor": None,
    "CrystalDewWorld.CrystalDiskInfo": None,
    "TechPowerUp.GPU-Z": None,
    "Guru3D.Afterburner": "msi",        # MSI Afterburner is MSI's product
    # -- dev hub ----------------------------------------------------
    "Python.Python.3.12": "python",
    "EclipseAdoptium.Temurin.21.JDK": "eclipseadoptium",
    "OpenJS.NodeJS.LTS": "nodedotjs",
    "Git.Git": "git",
    "MSYS2.MSYS2": "gnu",               # GCC/MinGW toolchain = the GNU mark
    "Microsoft.VisualStudioCode": None,
    "Anysphere.Cursor": None,
    "JetBrains.PyCharm.Community": "pycharm",
    "JetBrains.IntelliJIDEA.Community": "intellijidea",
    "Apache.NetBeans": "apachenetbeanside",
    "Ollama.Ollama": "ollama",
    "OpenWebUI.OpenWebUI": None,
    "DBeaver.DBeaver.Community": "dbeaver",
    "Postman.Postman": "postman",
    "Bruno.Bruno": "bruno",
    "Docker.DockerDesktop": "docker",
}


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "pulse-icon-fetch"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read()


def _slugify(title: str) -> str:
    out = title.lower().replace("+", "plus").replace(".", "dot").replace("&", "and")
    return "".join(ch for ch in out if ch.isalnum())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                        help="re-download icons that are already present")
    args = parser.parse_args()

    os.makedirs(ASSET_DIR, exist_ok=True)
    print(f"index  <- {INDEX_URL}")
    raw = json.loads(_get(INDEX_URL).decode("utf-8"))
    entries = raw["icons"] if isinstance(raw, dict) else raw
    # the published index leaves `slug` blank for most entries, so derive
    # it the same way Simple Icons does and key on that
    index = {(e.get("slug") or _slugify(e.get("title", ""))): e for e in entries}
    print(f"       {len(index)} brand marks available")

    manifest: dict[str, dict] = {}
    fetched = skipped = 0
    unmapped: list[str] = []

    for app_id, slug in sorted(ICON_MAP.items()):
        if slug is None:
            unmapped.append(app_id)
            continue
        entry = index.get(slug)
        if entry is None:
            print(f"  !! {app_id}: slug {slug!r} is not in the index")
            unmapped.append(app_id)
            continue
        # File name is the APP ID, not the slug: the runtime looks up by
        # the id it already has, and a hand-supplied override drops in at
        # the same path with no manifest edit.
        safe = app_id.replace("/", "_").replace("\\", "_")
        path = os.path.join(ASSET_DIR, f"{safe}.svg")
        if args.force or not os.path.isfile(path):
            data = _get(ICON_URL.format(slug=slug))
            if b"<svg" not in data[:400]:
                print(f"  !! {app_id}: {slug}.svg was not an SVG, skipped")
                unmapped.append(app_id)
                continue
            with open(path, "wb") as handle:
                handle.write(data)
            fetched += 1
        else:
            skipped += 1
        manifest[app_id] = {
            "file": f"{safe}.svg",
            "hex": "#" + str(entry.get("hex", "000000")).lstrip("#"),
            "title": entry.get("title", ""),
        }

    with open(MANIFEST, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=1, sort_keys=True)

    print(f"\nfetched {fetched}, already present {skipped}")
    print(f"manifest -> {os.path.relpath(MANIFEST, ROOT)} ({len(manifest)} logos)")
    if unmapped:
        print(f"\nno brand asset ({len(unmapped)}) — these fall back to the "
              "installed app's own icon, then to the neutral glyph:")
        for app_id in unmapped:
            print(f"    {app_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
