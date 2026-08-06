"""
src/utils/updater.py

THE SELF-UPDATER (v10.3) — check, verify, then hand over.

Pulse is a system-repair tool that elevates ~24 of its tasks. An updater
for it is therefore a security component first and a convenience second,
and the whole module is arranged around one rule:

    NOTHING DOWNLOADED IS EVER EXECUTED UNTIL ITS SHA-256 MATCHES A DIGEST
    PUBLISHED IN THE RELEASE.

A tool that runs elevated code, downloading an installer over the network
and launching it, is exactly the shape of a supply-chain compromise. HTTPS
alone does not close that: it authenticates the host, not the artifact, and
it says nothing about a release asset replaced after publication. So
tools/build_release.ps1 emits SHA256SUMS beside the installer, the release
publishes both, and verify() refuses anything that does not match. A
release without SHA256SUMS is a release this updater declines — silently
and correctly.

EVERY NETWORK FAILURE IS SILENT
    check() returns None for offline, DNS failure, timeout, HTTP 403 (the
    unauthenticated GitHub API allows 60 requests/hour/IP), malformed JSON,
    a missing asset, anything. This is not defensive habit: Pulse routinely
    runs on machines that are broken, freshly imaged, behind a captive
    portal or deliberately offline. An update check that produces an error
    the user has to dismiss would be a worse bug than never checking.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
    No Qt. It is pure logic — network, parsing, comparison, hashing — so
    every rule above is testable without a GUI, an event loop or a
    display. The dialog and the QThread that drives it live in the
    frontend; this is what they call.

    No `requests`. requirements.txt is one line (PySide6) and stays that
    way: urllib does a JSON GET and a streamed download perfectly well,
    and ssl.create_default_context() already validates against the Windows
    certificate store.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from utils import resources, version

#: The repository releases are published from.
REPO = "Humam-Taibeh/Humam-Windows-Architecture"
_API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
_API_LIST = f"https://api.github.com/repos/{REPO}/releases?per_page=10"

#: GitHub 403s a request with no User-Agent. Naming the app and version
#: also means the API traffic is attributable if it ever misbehaves.
_USER_AGENT = f"PULSE/{version.VERSION} (+https://github.com/{REPO})"

_CONNECT_TIMEOUT = 5.0
_READ_TIMEOUT = 10.0

#: The installer asset, as tools/build_release.ps1 names it.
_ASSET_RE = re.compile(r"^PULSE_Setup_v\d+\.\d+\.\d+\.exe$", re.IGNORECASE)
#: The digest list that same script emits. Its absence blocks the update.
_SUMS_ASSET = "SHA256SUMS"

#: Refuse anything absurd before writing it to disk. The real installer is
#: ~50-90MB; the ceiling is generous but finite, because "stream until the
#: server stops" is how a download becomes a disk-filling denial of service.
_MAX_ASSET_BYTES = 400 * 1024 * 1024

#: A SHA256SUMS line: "<64 hex>  <filename>" (sha256sum's own format).
_SUMS_LINE = re.compile(r"^([0-9a-fA-F]{64})\s+\*?(.+)$")


class UpdateError(Exception):
    """Raised only by download()/verify()/apply() — the steps the user
    explicitly asked for and is watching. check() never raises."""


@dataclass(frozen=True)
class Update:
    """A release newer than the running build, ready to be offered."""
    version: str            # "10.4.0" — normalised, no leading v
    tag: str                # "v10.4.0" — as GitHub has it
    notes: str              # release body, plain text
    url: str                # browser_download_url of the installer
    size: int               # bytes, as GitHub reports them
    sums_url: str | None    # browser_download_url of SHA256SUMS
    asset_name: str
    prerelease: bool
    published_at: str = ""

    @property
    def size_mb(self) -> float:
        return self.size / (1024 * 1024)


# ============================================================
#  CHECK
# ============================================================
def _get(url: str, timeout: float = _READ_TIMEOUT) -> bytes | None:
    request = urllib.request.Request(url, headers={
        "User-Agent": _USER_AGENT,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout,
                                    context=context) as response:
            return response.read(_MAX_ASSET_BYTES)
    except (urllib.error.URLError, urllib.error.HTTPError, ssl.SSLError,
            TimeoutError, OSError, ValueError):
        # EVERY failure mode lands here on purpose — see the module
        # docstring. Offline, DNS, timeout, 403 rate-limit, a proxy serving
        # an HTML error page: none of them is worth a dialog.
        return None


def _pick_release(payload, channel: str) -> dict | None:
    """The newest release this channel may be offered.

    A STABLE BUILD IS NEVER OFFERED A PRERELEASE. GitHub's
    `releases/latest` already excludes them, but a Beta build asks for the
    full list, and that list contains both — so the filter has to exist
    here rather than be inherited from which endpoint was called.
    """
    releases = payload if isinstance(payload, list) else [payload]
    best = None
    best_key = (0, 0, 0)
    for release in releases:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        if release.get("prerelease") and channel.lower() not in ("beta", "dev"):
            continue
        key = version.parse(release.get("tag_name") or "")
        if key > best_key:
            best, best_key = release, key
    return best


def _find_assets(release: dict) -> tuple[dict | None, dict | None]:
    installer = sums = None
    for asset in release.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name") or ""
        if _ASSET_RE.match(name):
            installer = asset
        elif name == _SUMS_ASSET:
            sums = asset
    return installer, sums


def check(current: str | None = None,
          channel: str | None = None) -> Update | None:
    """Is there a newer release? `None` for "no", and for every failure.

    Returns None rather than raising in all of: no network, DNS failure,
    timeout, rate limit, malformed JSON, no matching asset, and a release
    that is not actually newer.
    """
    current = current or version.VERSION
    channel = channel or version.CHANNEL

    url = _API_LIST if channel.lower() in ("beta", "dev") else _API_LATEST
    raw = _get(url)
    if raw is None:
        return None
    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        return None

    release = _pick_release(payload, channel)
    if not release:
        return None

    tag = str(release.get("tag_name") or "")
    if not version.is_newer(tag, current):
        return None

    installer, sums = _find_assets(release)
    if not installer or not installer.get("browser_download_url"):
        # A release with notes but no installer is one that is still being
        # published, or one built without the release script. Offering it
        # would send the user to a download that does not exist.
        return None

    size = int(installer.get("size") or 0)
    if size <= 0 or size > _MAX_ASSET_BYTES:
        return None

    return Update(
        version=".".join(str(p) for p in version.parse(tag)),
        tag=tag,
        notes=_clean_notes(release.get("body") or ""),
        url=str(installer["browser_download_url"]),
        size=size,
        sums_url=(str(sums["browser_download_url"])
                  if sums and sums.get("browser_download_url") else None),
        asset_name=str(installer.get("name") or ""),
        prerelease=bool(release.get("prerelease")),
        published_at=str(release.get("published_at") or ""),
    )


def _clean_notes(body: str, limit: int = 8000) -> str:
    """Release notes as PLAIN TEXT, never markup.

    The body is attacker-influenceable content: anyone who can publish a
    release, or anyone who compromises the account, chooses this string.
    Rendering it as HTML in a Qt rich-text widget would let it load remote
    images (a read receipt at minimum) and follow links. So the light
    Markdown that release notes actually use is reduced to text here, and
    the dialog renders it with rich text switched off.
    """
    text = body.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)   # headings
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)                 # bold
    text = re.sub(r"(?<!\w)[*_](.+?)[*_](?!\w)", r"\1", text)    # italics
    text = re.sub(r"`{1,3}([^`]*)`{1,3}", r"\1", text)           # code
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)             # images
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)         # links
    text = re.sub(r"<[^>]+>", "", text)                          # raw HTML
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "\n\n[...]"
    return text


# ============================================================
#  DOWNLOAD
# ============================================================
def download_dir() -> str:
    """%LOCALAPPDATA%\\PULSE\\updates — outside the install directory.

    Deliberately NOT beside the executable. Program Files is not writable
    without elevation (which is the point of installing there), and a
    downloads folder that only works for administrators would push the
    updater toward asking for rights it does not need until the moment it
    launches Setup.
    """
    path = os.path.join(resources.local_appdata(), "PULSE", "updates")
    os.makedirs(path, exist_ok=True)
    return path


def download(update: Update, progress=None, cancel=None) -> str:
    """Stream the installer to disk and return its path.

    `progress(received, total)` is called as bytes arrive; `cancel()` is
    polled and, if it returns True, the partial file is removed and
    UpdateError raised. Both are plain callables so this stays Qt-free —
    the dialog wires them to a signal and a flag.
    """
    target = os.path.join(download_dir(), update.asset_name)
    partial = target + ".part"

    request = urllib.request.Request(update.url,
                                     headers={"User-Agent": _USER_AGENT})
    context = ssl.create_default_context()
    received = 0
    try:
        with urllib.request.urlopen(request, timeout=_READ_TIMEOUT,
                                    context=context) as response, \
                open(partial, "wb") as handle:
            total = int(response.headers.get("Content-Length") or update.size)
            while True:
                if cancel is not None and cancel():
                    raise UpdateError("cancelled")
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > _MAX_ASSET_BYTES:
                    raise UpdateError("the download exceeded the size limit")
                handle.write(chunk)
                if progress is not None:
                    progress(received, total)
    except UpdateError:
        _unlink(partial)
        raise
    except (urllib.error.URLError, urllib.error.HTTPError, ssl.SSLError,
            TimeoutError, OSError, ValueError) as exc:
        _unlink(partial)
        raise UpdateError(f"the download failed: {exc}") from exc

    # The size GitHub advertised is a free, early integrity signal — a
    # truncated download fails here rather than at the digest, with a
    # message that says which.
    if update.size and received != update.size:
        _unlink(partial)
        raise UpdateError(
            f"the download is {received} bytes, expected {update.size}")

    _unlink(target)
    os.replace(partial, target)
    return target


def _unlink(path: str):
    try:
        os.remove(path)
    except OSError:
        pass


# ============================================================
#  VERIFY
# ============================================================
def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_sums(text: str) -> dict[str, str]:
    """`SHA256SUMS` -> {filename: digest}. Unparseable lines are ignored;
    a file whose name is not in the result simply fails verification."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        match = _SUMS_LINE.match(line.strip())
        if match:
            out[os.path.basename(match.group(2).strip())] = \
                match.group(1).lower()
    return out


def verify(path: str, update: Update) -> None:
    """Raise UpdateError unless `path` is exactly the published artifact.

    THE FAILURE MODES ARE ALL FATAL ON PURPOSE. A missing SHA256SUMS asset
    is not "verify what we can" — it is an unverifiable executable that
    this tool would otherwise run on a machine where it can elevate. The
    only safe response to "I cannot prove this is the right file" is to
    refuse and delete it.
    """
    if update.sums_url is None:
        raise UpdateError(
            "this release publishes no SHA256SUMS, so the download cannot "
            "be verified. Install it manually from the releases page if you "
            "trust it.")

    raw = _get(update.sums_url)
    if raw is None:
        raise UpdateError("the checksum file could not be downloaded")

    sums = parse_sums(raw.decode("utf-8", "replace"))
    expected = sums.get(os.path.basename(path)) or sums.get(update.asset_name)
    if not expected:
        raise UpdateError(
            f"SHA256SUMS does not list {update.asset_name}")

    actual = sha256(path)
    if actual != expected:
        raise UpdateError(
            "the download's SHA-256 does not match the published digest — "
            "it is corrupt or has been tampered with")


def authenticode_publisher(path: str) -> str | None:
    """The signing subject of `path`, or None if unsigned/invalid.

    Advisory today because the installer ships unsigned (see the SignTool
    note in installer/pulse.iss). It exists now so that the moment a
    certificate is in play, requiring a publisher match is a one-line
    change here rather than a new feature.
    """
    script = (
        "$s = Get-AuthenticodeSignature -LiteralPath $args[0]; "
        "if ($s.Status -eq 'Valid') { $s.SignerCertificate.Subject }")
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", script, path],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    out = (result.stdout or "").strip()
    return out or None


# ============================================================
#  APPLY
# ============================================================
def can_apply() -> tuple[bool, str]:
    """May this build hand over to an installer at all?

    A SOURCE CHECKOUT MUST NEVER BE 'UPDATED'. Running Setup over a
    developer's working tree would install a release build beside it and
    do nothing to the code they are actually running — confusing at best,
    and on a machine mid-debug, destructive of the thing under test.
    """
    if not getattr(sys, "frozen", False):
        return False, ("Pulse is running from source; update it with git "
                       "rather than an installer.")
    return True, ""


def apply(path: str, silent: bool = True) -> None:
    """Launch the verified installer and leave. The caller quits Pulse.

    WINDOWS CANNOT OVERWRITE A RUNNING EXECUTABLE, which is why this is a
    hand-off rather than an in-process update. pulse.iss sets
    CloseApplications/RestartApplications so Setup closes the running copy,
    replaces it and starts it again.

    `path` must already have been through verify(). This function does not
    re-check, and that is deliberate: two places deciding whether an
    executable is trustworthy is one place too many to keep correct.
    """
    ok, why = can_apply()
    if not ok:
        raise UpdateError(why)
    if not os.path.isfile(path):
        raise UpdateError("the installer is no longer on disk")

    args = [path]
    if silent:
        # /SILENT shows a progress window but asks nothing; /NOCANCEL stops
        # a half-applied install; /NORESTART leaves reboot policy to us.
        args += ["/SILENT", "/NOCANCEL", "/NORESTART"]
    try:
        subprocess.Popen(args, close_fds=True,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    except OSError as exc:
        raise UpdateError(f"the installer could not be started: {exc}") from exc


def prune(keep: str | None = None, max_age_days: float = 7.0) -> int:
    """Delete stale downloads. Returns how many went.

    An installer is ~50-90MB and is useless the moment it has run, so
    without this the updates folder grows by one release forever.
    """
    removed = 0
    cutoff = time.time() - max_age_days * 86400
    try:
        entries = os.listdir(download_dir())
    except OSError:
        return 0
    for name in entries:
        path = os.path.join(download_dir(), name)
        if keep and os.path.normcase(path) == os.path.normcase(keep):
            continue
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            continue
    return removed
