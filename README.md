# dj_request

Guests paste a song link on their phone, and it lands as an AIFF in your DJ folder.

```
Guest phone → GitHub Pages form (docs/) → Firestore "requests"
                                                │ realtime listener (push, no polling)
                                                ▼
                         Laptop helper (helper/dj_helper.py)
                           youtube → yt-dlp     apple → gamdl
                                                │
                                        ffmpeg → AIFF → your folder
```

Each request moves through `pending → downloading → done | failed`, and the
guest's page shows the status live as a tracker (wording depends on the theme).
Each finished download also pops up a macOS notification with the song and who asked for it.

| Service      | How it's downloaded | Single-song links accepted |
|--------------|---------------------|----------------------------|
| YouTube      | `yt-dlp` | `watch?v=`, `youtu.be/`, `shorts/`, music.youtube.com |
| SoundCloud   | `yt-dlp` | `soundcloud.com/<artist>/<track>`, `on.soundcloud.com` share links |
| Apple Music  | `gamdl`  | `/song/…` or album links with `?i=` |
| Spotify      | matched to Apple Music by title, artist and length (±3s) and downloaded with `gamdl`; falls back to `spotDL` (YouTube audio) if there's no match | `/track/…`, `spotify.link` share links |

**Playlist protection** is in the helper only. For guests, playlists, albums,
sets, and artist/profile pages are refused, short share links are expanded
before the check, and a request that would produce more than one track is
refused.

**DJ requests can be playlists.** Playlists, albums and SoundCloud sets are
allowed when the request comes from you:
- **On the site:** sign in to the admin panel with your admin account. The
  form header changes (e.g. "DJ pass"), and your requests are marked as DJ
  requests. Firestore rules only accept that mark from an admin account.
- **On the laptop:** paste a link into the helper's terminal and press Enter.

Artist pages and profiles are still refused, even for DJ requests. Spotify
playlists and albums download through spotDL (YouTube audio).

**Duplicate protection.** Before saving, the helper checks your whole music
library (`~/Documents/Music` by default; use `--library DIR` to change it, more
than once for several folders). That includes songs from your own playlists
and files you added by hand. If the same song is already there, it isn't
saved again. A match means the same artist, the same base title (ignoring
"(Official Video)", "- Remastered", "feat." and so on), and a length within
5 seconds, so a radio edit and an extended mix are both kept. The guest still
sees "Landed", and you get an "Already in your crate" notification. What's
been read is cached in `helper/.library-cache.json`, so restarts are instant.

SoundCloud Go+ tracks only offer a 30-second preview. The helper refuses
these instead of saving a clip.

## One-time setup

### 1. Firebase
1. Create a project at <https://console.firebase.google.com> (Analytics not needed).
2. **Build → Firestore Database → Create database** in production mode.
3. **Firestore → Rules**: paste the contents of `firestore.rules` and publish.
4. **Project settings → General → Your apps → Add app → Web**. Copy the config
   object into `docs/firebase-config.js`.
5. **Project settings → Service accounts → Generate new private key**. Save it
   as `helper/service-account.json`. It's gitignored, so never commit it.
6. **Build → Authentication → Get started → Google**: enable it. Under
   **Settings → Authorized domains**, add `iansjackson.com`. This is only needed
   for the admin panel.

### 2. GitHub Pages
Repo **Settings → Pages → Deploy from a branch → `main` / `/docs`**.
The form will be at `https://iansjackson.com/dj_request/`.

### 3. Laptop helper
Requires `yt-dlp`, `gamdl` and `ffmpeg` on your PATH.

```sh
python3 -m venv .venv
.venv/bin/pip install -r helper/requirements.txt
```

For Apple Music, put your `cookies.txt` (Netscape format) at
`helper/cookies.txt`, or pass `--cookies PATH`.

## At the gig

```sh
.venv/bin/python helper/dj_helper.py ~/Music/Requests
```

Requests sent while the helper is off are picked up as soon as it starts.

Notifications are silent by default. Add `--notify-sound` for a chime (check it
won't play through the PA), or use `--no-notify` to turn them off.

## Themes

Each gig can have its own look. The theme sets the page's wording, colours,
icon and background. Pick one from the **Theme** menu in the admin panel. Every
open guest page switches right away.

| Key | Name | Look |
|-----|------|------|
| `party` | Party (generic), the default | Neon dance floor with equalizer bars. "Request → Queued → Ready" |
| `fleetweek` | SF Fleet Week | Blue Angels over the Golden Gate. "Boarding pass → Tower → Landed" |

To add a theme:
1. Add an entry to `THEMES` in `docs/theme.js`. Copy an existing one and
   change the text, icon path, scene markup and favicon.
2. Add a `html[data-theme="<key>"]` block to `docs/style.css` that sets the
   colour tokens (`--bg`, `--accent`, `--card`…) and any styles for the scene.

The theme is stored in Firestore at `config/state.theme`. Pausing from the
helper keeps it.

## Pausing requests

When paused, the page shows a "paused" banner and Firestore rejects new requests.
Requests already in the queue still download.

- **From the laptop:** in the helper's terminal, type `p` + Enter to pause, `r` to
  resume, or `s` for status. Paste a link to queue it as a DJ request.
- **From any browser:** type `admin` into the song-link field and submit. This
  opens the admin panel, which has a pause/resume switch, the theme picker and
  a live list of requests. Sign in with Google. The first time, grant your account access from
  the laptop, then reload the page:

  ```sh
  .venv/bin/python helper/dj_helper.py --grant-admin you@gmail.com
  ```

  The code only reveals the panel. Only accounts granted admin can use it.
  To change the code, edit `ADMIN_CODE` in `docs/app.js`.

Test a download without Firebase:

```sh
.venv/bin/python helper/dj_helper.py ~/Music/Requests --test "https://youtu.be/…"
```

## Deploying site changes

GitHub Pages lets browsers cache files for 10 minutes, and that can't be
changed. To make sure guests get new code right away, stamp a new version
before committing changes to `docs/`:

```sh
./scripts/stamp-version.sh
```

This updates the `?v=` on every asset link and writes `docs/version.json`.
Open pages check that file every minute, and whenever the tab comes back into
view, and reload themselves onto the new version.
