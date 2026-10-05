#!/usr/bin/env python3
"""DJ request helper.

Listens to the Firestore `requests` collection for new song links, downloads
them with the right CLI for the platform, converts to AIFF, and drops the
result into the output folder.

    python dj_helper.py ~/Music/Requests
    python dj_helper.py ~/Music/Requests --test "https://youtu.be/..."
    python dj_helper.py --grant-admin you@gmail.com

While running, type p + Enter to pause requests, r to resume, s for status.
"""

import argparse
import json
from html import unescape
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
from pathlib import Path

# Quiet gRPC's harmless fork() warnings when we shell out to the downloaders.
os.environ.setdefault("GRPC_VERBOSITY", "ERROR")

log = logging.getLogger("dj_helper")

COLLECTION = "requests"
STATE_DOC = ("config", "state")  # {accepting: bool}; guests can only submit while true
DOWNLOAD_TIMEOUT_S = 300
AUDIO_EXTS = {".m4a", ".mp3", ".opus", ".ogg", ".webm", ".flac", ".wav", ".aac", ".mp4"}

APPLE_STOREFRONT = "us"  # must match the country of the Apple Music account in cookies.txt

# Single-song links only. The page and firestore.rules only check the domain;
# this is the one place that decides what counts as a single song.
SONG_LINKS = {
    "youtube": re.compile(
        r"^https://((www\.|m\.|music\.)?youtube\.com/(watch\?\S*\bv=[\w-]+\S*|(shorts|live)/[\w-]+([?#]\S*)?)"
        r"|youtu\.be/[\w-]+([?#]\S*)?)$"),
    # /song/... or an album link pointing at one track (?i=...).
    "apple": re.compile(r"^https://music\.apple\.com/\S*(/song/|[?&]i=\d+)\S*$"),
    # soundcloud.com/<artist>/<track>, optionally a private /s-xxxx share token.
    "soundcloud": re.compile(
        r"^https://(www\.|m\.)?soundcloud\.com/[^\s/?#]+/"
        r"(?!(sets|likes|tracks|reposts|albums|popular-tracks|followers|following|comments|spotlight|toptracks)([/?#]|$))"
        r"[^\s/?#]+(/s-[A-Za-z0-9]+)?/?([?#]\S*)?$"),
    "spotify": re.compile(r"^https://open\.spotify\.com/(intl-[a-z-]+/)?track/[A-Za-z0-9]+/?([?#]\S*)?$"),
}

# App share links that redirect somewhere else; we expand them before deciding.
SHORT_LINKS = re.compile(r"^https://(on\.soundcloud\.com|spotify\.link)/[A-Za-z0-9]+/?$")

# Recognisable multi-track links, so guests get a clear "no playlists" message.
PLAYLIST_LINKS = re.compile(
    r"youtube\.com/(playlist|@|channel/|c/|user/)"
    r"|music\.apple\.com/\S*/(album|playlist|artist|curator|station)/"
    r"|soundcloud\.com/[^\s/?#]+(/(sets|likes|tracks|reposts|albums|popular-tracks|toptracks|spotlight)\b|/?$)"
    r"|open\.spotify\.com/(intl-[a-z-]+/)?(album|playlist|artist|show|episode|user)/")


class RejectedLink(ValueError):
    """A link refused on purpose. `reason` is shown to the guest."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def classify(url: str) -> str:
    for name, pattern in SONG_LINKS.items():
        if pattern.match(url):
            return name
    if PLAYLIST_LINKS.search(url):
        raise RejectedLink("playlist", "playlists, albums and profiles aren't allowed, one song per request")
    raise RejectedLink("unsupported", f"unsupported link: {url}")


def http_get(url: str, timeout: int = 15) -> tuple[str, str]:
    """Return (final URL after redirects, body)."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh)"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.geturl(), res.read(500_000).decode("utf-8", "replace")


def expand_short_link(url: str) -> str:
    final, body = http_get(url)
    if not SHORT_LINKS.match(final):
        return final
    # Some short links redirect with JavaScript; take the target from the page.
    found = re.search(r"https://(open\.spotify\.com|(www\.)?soundcloud\.com)/[^\s\"'<>\\]+", body)
    if not found:
        raise RejectedLink("unsupported", f"couldn't expand short link: {url}")
    return found.group(0)


def resolve_link(url: str) -> tuple[str, str]:
    """Return (platform, url) for a single-song link, or raise RejectedLink."""
    if SHORT_LINKS.match(url):
        expanded = expand_short_link(url)
        log.info("  expanded %s -> %s", url, expanded)
        url = expanded
    return classify(url), url


# --- downloaders -------------------------------------------------------------

def download_ytdlp(url: str, workdir: Path) -> None:
    """YouTube and SoundCloud."""
    try:
        run([
            "yt-dlp",
            "--no-playlist",
            "--playlist-items", "1",  # belt and braces: never more than one track
            # SoundCloud Go+ tracks only expose 30s previews; never hand the DJ a clip.
            "-f", "bestaudio[format_id!*=preview]",
            "-x",
            "--embed-metadata",
            "--embed-thumbnail",
            "-o", str(workdir / "%(title)s.%(ext)s"),
            url,
        ])
    except RuntimeError as exc:
        if "Requested format is not available" in str(exc):
            raise RuntimeError("only a 30-second preview is available (SoundCloud Go+ track)") from exc
        raise


def download_apple(url: str, workdir: Path, cookies: Path | None) -> None:
    cmd = ["gamdl", "-o", str(workdir), "--temp-path", str(workdir / ".gamdl-tmp")]
    if cookies:
        cmd += ["-c", str(cookies)]
    run(cmd + [url])


def spotify_track_info(url: str) -> dict:
    """Title, artist and duration from the public Spotify track page's meta tags."""
    _, html = http_get(url)

    def meta(key: str) -> str | None:
        m = re.search(rf'<meta (?:property|name)="{re.escape(key)}" content="([^"]*)"', html)
        return unescape(m.group(1)) if m else None

    title, artist, duration = meta("og:title"), meta("music:musician_description"), meta("music:duration")
    if not (title and artist and duration):
        raise RuntimeError("couldn't read track details from the Spotify page")
    return {"title": title, "artist": artist, "duration": int(duration)}


def base_title(title: str) -> str:
    """'Song - Remastered 2011' / 'Song (feat. X)' -> 'song', for comparing across services."""
    title = re.sub(r"\s[-–]\s.*$", "", title)
    title = re.sub(r"[(\[].*?[)\]]", "", title)
    return re.sub(r"[^a-z0-9]", "", title.lower().replace("&", "and"))


def spotify_to_apple(url: str) -> str | None:
    """Find the same recording on Apple Music: same base title, artist and length (±3s)."""
    info = spotify_track_info(url)
    query = urllib.parse.urlencode({"term": f"{info['artist']} {info['title']}", "entity": "song",
                                    "limit": 25, "country": APPLE_STOREFRONT})
    _, body = http_get(f"https://itunes.apple.com/search?{query}")
    want_title = base_title(info["title"])
    want_artist = re.sub(r"[^a-z0-9]", "", info["artist"].lower())
    for r in json.loads(body).get("results", []):
        artist = re.sub(r"[^a-z0-9]", "", r.get("artistName", "").lower())
        if (base_title(r.get("trackName", "")) == want_title
                and (want_artist in artist or artist in want_artist)
                and abs(r.get("trackTimeMillis", 0) / 1000 - info["duration"]) <= 3):
            log.info("  matched %s - %s on apple music", r["artistName"], r["trackName"])
            return f"https://music.apple.com/{APPLE_STOREFRONT}/song/{r['trackId']}"
    return None


def download_spotify(url: str, workdir: Path, cookies: Path | None) -> None:
    """Spotify audio is DRM'd, so grab the same song from Apple Music, else YouTube via spotDL."""
    try:
        apple_url = spotify_to_apple(url)
    except Exception as exc:  # noqa: BLE001 - a failed lookup just means using the fallback
        log.warning("  apple music lookup failed: %s", exc)
        apple_url = None
    if apple_url:
        log.info("  spotify -> apple music %s", apple_url)
        try:
            download_apple(apple_url, workdir, cookies)
            return
        except RuntimeError as exc:
            log.warning("  apple music download failed, falling back to spotDL: %s", exc)
    else:
        log.info("  not on apple music, falling back to spotDL")
    spotdl = shutil.which("spotdl") or str(Path(sys.executable).parent / "spotdl")
    run([spotdl, "download", url, "--output", str(workdir / "{artists} - {title}.{output-ext}")])


def run(cmd: list[str]) -> None:
    log.debug("$ %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=DOWNLOAD_TIMEOUT_S)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-5:]
        raise RuntimeError(f"{cmd[0]} failed: " + " | ".join(tail))


# --- conversion --------------------------------------------------------------

def read_tags(src: Path, tag_source: str) -> dict[str, str]:
    section = "stream_tags" if tag_source != "0" else "format_tags"
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
         f"{section}=artist,title", "-of", "json", str(src)],
        capture_output=True, text=True,
    )
    try:
        data = json.loads(proc.stdout)
        tags = data["streams"][0]["tags"] if section == "stream_tags" else data["format"]["tags"]
    except (ValueError, KeyError, IndexError):
        return {}
    return {k.lower(): v for k, v in tags.items()}


def output_name(src: Path, tags: dict[str, str]) -> str:
    artist, title = tags.get("artist", "").strip(), tags.get("title", "").strip()
    name = f"{artist} - {title}" if artist and title else src.stem
    return re.sub(r'[/\\:*?"<>|]', "_", name)[:180]


def to_aiff(src: Path, out_dir: Path) -> Path:
    # Ogg/Opus keep tags on the audio stream rather than the container.
    tag_source = "0:s:a:0" if src.suffix.lower() in {".opus", ".ogg"} else "0"
    dest = unique_path(out_dir / f"{output_name(src, read_tags(src, tag_source))}.aiff")
    base = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src)]
    audio = ["-map_metadata", tag_source, "-c:a", "pcm_s16be", "-write_id3v2", "1"]
    with_cover = base + ["-map", "0:a:0", "-map", "0:v:0?", "-c:v", "copy",
                         "-disposition:v", "attached_pic"] + audio + [str(dest)]
    try:
        run(with_cover)
    except RuntimeError:
        # Some cover art streams don't mux into AIFF; keep the audio + tags.
        log.warning("cover art embed failed for %s, retrying without it", src.name)
        run(base + ["-map", "0:a:0"] + audio + [str(dest)])
    return dest


def unique_path(path: Path) -> Path:
    candidate, n = path, 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate


def fetch(url: str, out_dir: Path, cookies: Path | None) -> list[Path]:
    """Download one song link and return the AIFF file written to out_dir."""
    platform, url = resolve_link(url)

    with tempfile.TemporaryDirectory(prefix="djreq-") as tmp:
        workdir = Path(tmp)
        if platform in ("youtube", "soundcloud"):
            download_ytdlp(url, workdir)
        elif platform == "apple":
            download_apple(url, workdir, cookies)
        elif platform == "spotify":
            download_spotify(url, workdir, cookies)

        sources = [p for p in workdir.rglob("*")
                   if p.suffix.lower() in AUDIO_EXTS and ".gamdl-tmp" not in p.parts]
        if not sources:
            raise RuntimeError("downloader finished but produced no audio file")
        if len(sources) > 1:
            # Should be impossible after the link checks; refuse rather than flood the crate.
            raise RejectedLink("playlist", f"link produced {len(sources)} tracks, one song per request")
        return [to_aiff(sources[0], out_dir)]


# --- notifications -----------------------------------------------------------

class Notifier:
    """macOS notification banners via osascript. Silent unless sound is asked for."""

    def __init__(self, enabled: bool, sound: bool):
        self.enabled = enabled and sys.platform == "darwin"
        self.sound = sound

    def send(self, title: str, subtitle: str, message: str) -> None:
        if not self.enabled:
            return
        # Strings go in as argv so quotes in song titles can't break the script.
        script = ["on run argv",
                  "display notification (item 3 of argv) with title (item 1 of argv)"
                  " subtitle (item 2 of argv)" + (' sound name "Glass"' if self.sound else ""),
                  "end run"]
        cmd = ["osascript"] + [arg for line in script for arg in ("-e", line)]
        subprocess.run(cmd + [title, subtitle, message], capture_output=True)


# --- Firestore ---------------------------------------------------------------

def init_firebase(credentials_path: Path):
    import firebase_admin
    from firebase_admin import credentials, firestore

    firebase_admin.initialize_app(credentials.Certificate(str(credentials_path)))
    return firestore.client()


def grant_admin(credentials_path: Path, email: str) -> None:
    from firebase_admin import auth

    init_firebase(credentials_path)
    user = auth.get_user_by_email(email)
    auth.set_custom_user_claims(user.uid, {"admin": True})
    print(f"{email} is now cleared for the control tower. Reload the page to pick it up.")


def set_accepting(state_ref, accepting: bool) -> None:
    from firebase_admin import firestore

    state_ref.set({"accepting": accepting, "updatedAt": firestore.SERVER_TIMESTAMP,
                   "updatedBy": "helper"})


def console_commands(state_ref, jobs: queue.Queue) -> None:
    """Read p / r / s commands typed into the helper's terminal."""
    for line in sys.stdin:
        cmd = line.strip().lower()
        if cmd in ("p", "pause"):
            set_accepting(state_ref, False)
        elif cmd in ("r", "resume"):
            set_accepting(state_ref, True)
        elif cmd in ("s", "status"):
            snap = state_ref.get()
            open_ = (snap.to_dict() or {}).get("accepting", True) if snap.exists else True
            log.info("airspace %s · %d request(s) waiting",
                     "OPEN" if open_ else "CLOSED", jobs.qsize())
        elif cmd:
            log.info("commands: p = pause requests, r = resume, s = status, Ctrl+C = quit")


def listen(out_dir: Path, credentials_path: Path, cookies: Path | None,
           notifier: Notifier) -> None:
    from firebase_admin import firestore
    from google.cloud.firestore_v1.base_query import FieldFilter

    db = init_firebase(credentials_path)
    requests = db.collection(COLLECTION)
    state_ref = db.collection(STATE_DOC[0]).document(STATE_DOC[1])
    if not state_ref.get().exists:
        set_accepting(state_ref, True)

    # Anything left mid-flight by a previous run goes back in the queue.
    for snap in requests.where(filter=FieldFilter("status", "==", "downloading")).stream():
        log.info("re-queuing interrupted request %s", snap.id)
        snap.reference.update({"status": "pending"})

    jobs: queue.Queue = queue.Queue()
    seen: set[str] = set()

    def on_requests(_docs, changes, _read_time):
        for change in changes:
            doc = change.document
            if change.type.name == "ADDED" and doc.id not in seen:
                seen.add(doc.id)
                jobs.put(doc)

    def on_state(docs, _changes, _read_time):
        data = (docs[0].to_dict() or {}) if docs and docs[0].exists else {}
        open_ = data.get("accepting", True)
        log.info("✈ airspace %s%s", "OPEN — taking requests" if open_ else "CLOSED — requests paused",
                 f" (by {data['updatedBy']})" if data.get("updatedBy") else "")

    watches = [
        requests.where(filter=FieldFilter("status", "==", "pending")).on_snapshot(on_requests),
        state_ref.on_snapshot(on_state),
    ]
    if sys.stdin.isatty():
        threading.Thread(target=console_commands, args=(state_ref, jobs), daemon=True).start()
    log.info("listening for requests -> %s", out_dir)
    log.info("type p + Enter to pause requests, r to resume, s for status, Ctrl+C to quit")

    try:
        while True:
            doc = jobs.get()
            data = doc.to_dict() or {}
            url = data.get("url", "")
            who = data.get("requester") or "anonymous"
            log.info("▶ %s (from %s)", url, who)
            doc.reference.update({"status": "downloading",
                                  "startedAt": firestore.SERVER_TIMESTAMP})
            try:
                files = fetch(url, out_dir, cookies)
            except RejectedLink as exc:
                log.warning("⛔ %s: %s", url, exc)
                doc.reference.update({"status": "failed", "reason": exc.reason, "error": str(exc),
                                      "finishedAt": firestore.SERVER_TIMESTAMP})
            except Exception as exc:  # noqa: BLE001 - report every failure to the guest
                log.error("✗ %s: %s", url, exc)
                doc.reference.update({"status": "failed", "error": str(exc)[:500],
                                      "finishedAt": firestore.SERVER_TIMESTAMP})
                notifier.send("⚠️ Mayday — request failed", f"from {who}", url)
            else:
                names = [f.stem for f in files]
                log.info("✓ %s", ", ".join(names))
                doc.reference.update({"status": "done", "files": [f.name for f in files],
                                      "finishedAt": firestore.SERVER_TIMESTAMP})
                notifier.send("✈️ Touchdown", f"from {who}", ", ".join(names))
    except KeyboardInterrupt:
        log.info("stopping")
    finally:
        for watch in watches:
            watch.unsubscribe()


def main() -> None:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out_dir", type=Path, nargs="?", help="folder to save AIFF files into")
    parser.add_argument("--credentials", type=Path,
                        default=Path(os.environ.get("DJ_HELPER_CREDENTIALS",
                                                    here / "service-account.json")),
                        help="Firebase service account JSON (default: helper/service-account.json)")
    default_cookies = here / "cookies.txt"
    parser.add_argument("--cookies", type=Path,
                        default=default_cookies if default_cookies.exists() else None,
                        help="Apple Music cookies.txt for gamdl (default: helper/cookies.txt)")
    parser.add_argument("--test", metavar="URL",
                        help="download a single link and exit, without Firebase")
    parser.add_argument("--grant-admin", metavar="EMAIL",
                        help="let this Google account use the site's control tower, then exit")
    parser.add_argument("--no-notify", action="store_true", help="disable macOS notifications")
    parser.add_argument("--notify-sound", action="store_true",
                        help="play a chime with notifications (careful: may hit the PA)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    if args.grant_admin:
        grant_admin(args.credentials, args.grant_admin)
        return

    if args.out_dir is None:
        parser.error("out_dir is required")
    out_dir = args.out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.test:
        try:
            for f in fetch(args.test, out_dir, args.cookies):
                print(f)
        except RejectedLink as exc:
            sys.exit(f"rejected ({exc.reason}): {exc}")
        return

    if not args.credentials.exists():
        parser.error(f"service account file not found: {args.credentials}")
    listen(out_dir, args.credentials, args.cookies,
           Notifier(enabled=not args.no_notify, sound=args.notify_sound))


if __name__ == "__main__":
    main()
