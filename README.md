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
guest's page shows the status live as a flight tracker (Tower → In flight → Landed).
Each finished download also pops up a macOS notification with the song and who asked for it.

| Service      | CLI      | Accepted links                                   |
|--------------|----------|--------------------------------------------------|
| YouTube      | `yt-dlp` | youtube.com, youtu.be, music.youtube.com         |
| Apple Music  | `gamdl`  | single songs: `/song/…` or album links with `?i=` |

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
   for the control tower.

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

## Pausing requests ("closing the airspace")

When paused, the page shows "Airspace closed" and Firestore rejects new requests.
Requests already in the queue still download.

- **From the laptop:** in the helper's terminal, type `p` + Enter to pause, `r` to
  resume, or `s` for status.
- **From any browser:** type `tower` into the song-link field and submit. This
  opens the Control Tower, which has a pause/resume switch and a live departures
  board. Sign in with Google. The first time, grant your account access from
  the laptop, then reload the page:

  ```sh
  .venv/bin/python helper/dj_helper.py --grant-admin you@gmail.com
  ```

  The code only reveals the panel. Only accounts granted admin can use it.
  To change the code, edit `TOWER_CODE` in `docs/app.js`.

Test a download without Firebase:

```sh
.venv/bin/python helper/dj_helper.py ~/Music/Requests --test "https://youtu.be/…"
```
