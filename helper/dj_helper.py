#!/usr/bin/env python3
"""DJ request helper.

Listens to the Firestore `requests` collection for new song links, downloads
them with the right CLI for the service, converts to AIFF, and drops the
result into the output folder.

    python dj_helper.py ~/Music/Requests
    python dj_helper.py ~/Music/Requests --test "https://youtu.be/..."
"""

import argparse
import json
import logging
import os
import queue
import re
import subprocess
import tempfile
from pathlib import Path

# Quiet gRPC's harmless fork() warnings when we shell out to the downloaders.
os.environ.setdefault("GRPC_VERBOSITY", "ERROR")

log = logging.getLogger("dj_helper")

COLLECTION = "requests"
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


# --- Firestore listener ------------------------------------------------------

def listen(out_dir: Path, credentials_path: Path, cookies: Path | None) -> None:
    import firebase_admin
    from firebase_admin import credentials, firestore
    from google.cloud.firestore_v1.base_query import FieldFilter

    firebase_admin.initialize_app(credentials.Certificate(str(credentials_path)))
    db = firestore.client()
    requests = db.collection(COLLECTION)

    # Anything left mid-flight by a previous run goes back in the queue.
    for snap in requests.where(filter=FieldFilter("status", "==", "downloading")).stream():
        log.info("re-queuing interrupted request %s", snap.id)
        snap.reference.update({"status": "pending"})

    jobs: queue.Queue = queue.Queue()
    seen: set[str] = set()

    def on_snapshot(_docs, changes, _read_time):
        for change in changes:
            doc = change.document
            if change.type.name == "ADDED" and doc.id not in seen:
                seen.add(doc.id)
                jobs.put(doc)

    watch = (requests
             .where(filter=FieldFilter("status", "==", "pending"))
             .on_snapshot(on_snapshot))
    log.info("listening for requests -> %s  (Ctrl+C to stop)", out_dir)

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
            else:
                names = [f.name for f in files]
                log.info("✓ %s", ", ".join(names))
                doc.reference.update({"status": "done", "files": names,
                                      "finishedAt": firestore.SERVER_TIMESTAMP})
    except KeyboardInterrupt:
        log.info("stopping")
    finally:
        watch.unsubscribe()


def main() -> None:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out_dir", type=Path, help="folder to save AIFF files into")
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
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    out_dir = args.out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.test:
        for f in fetch(args.test, out_dir, args.cookies):
            print(f)
        return

    if not args.credentials.exists():
        parser.error(f"service account file not found: {args.credentials}")
    listen(out_dir, args.credentials, args.cookies)


if __name__ == "__main__":
    main()
