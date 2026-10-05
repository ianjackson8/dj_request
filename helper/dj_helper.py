#!/usr/bin/env python3
"""DJ request helper.

Listens to the Firestore `requests` collection for new song links, downloads
them with the right CLI for the service, converts to AIFF, and drops the
result into the output folder.

    python dj_helper.py ~/Music/Requests
    python dj_helper.py ~/Music/Requests --test "https://youtu.be/..."
    python dj_helper.py --grant-admin you@gmail.com

While running, type p + Enter to pause requests, r to resume, s for status.
"""

import argparse
import json
import logging
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

# Quiet gRPC's harmless fork() warnings when we shell out to the downloaders.
os.environ.setdefault("GRPC_VERBOSITY", "ERROR")

log = logging.getLogger("dj_helper")

COLLECTION = "requests"
STATE_DOC = ("config", "state")  # {accepting: bool}; guests can only submit while true
DOWNLOAD_TIMEOUT_S = 300
AUDIO_EXTS = {".m4a", ".mp3", ".opus", ".ogg", ".webm", ".flac", ".wav", ".aac", ".mp4"}

# Keep in sync with docs/app.js and firestore.rules.
SERVICES = {
    "youtube": re.compile(r"^https://(www\.|m\.|music\.)?(youtube\.com|youtu\.be)/\S+$"),
    # Single songs only: /song/... or an album link pointing at one track (?i=...).
    "apple": re.compile(r"^https://music\.apple\.com/\S*(/song/|[?&]i=\d+)\S*$"),
}


def detect_service(url: str) -> str | None:
    for name, pattern in SERVICES.items():
        if pattern.match(url):
            return name
    return None


# --- downloaders -------------------------------------------------------------

def download_youtube(url: str, workdir: Path) -> None:
    run([
        "yt-dlp",
        "--no-playlist",
        "-f", "bestaudio",
        "-x",
        "--embed-metadata",
        "--embed-thumbnail",
        "-o", str(workdir / "%(title)s.%(ext)s"),
        url,
    ])


def download_apple(url: str, workdir: Path, cookies: Path | None) -> None:
    cmd = ["gamdl", "-o", str(workdir), "--temp-path", str(workdir / ".gamdl-tmp")]
    if cookies:
        cmd += ["-c", str(cookies)]
    run(cmd + [url])


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
    """Download one link and return the AIFF files written to out_dir."""
    service = detect_service(url)
    if service is None:
        raise ValueError(f"unsupported link: {url}")

    with tempfile.TemporaryDirectory(prefix="djreq-") as tmp:
        workdir = Path(tmp)
        if service == "youtube":
            download_youtube(url, workdir)
        elif service == "apple":
            download_apple(url, workdir, cookies)

        sources = [p for p in workdir.rglob("*")
                   if p.suffix.lower() in AUDIO_EXTS and ".gamdl-tmp" not in p.parts]
        if not sources:
            raise RuntimeError("downloader finished but produced no audio file")
        return [to_aiff(src, out_dir) for src in sources]


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
        for f in fetch(args.test, out_dir, args.cookies):
            print(f)
        return

    if not args.credentials.exists():
        parser.error(f"service account file not found: {args.credentials}")
    listen(out_dir, args.credentials, args.cookies,
           Notifier(enabled=not args.no_notify, sound=args.notify_sound))


if __name__ == "__main__":
    main()
