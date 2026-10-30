import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import {
  getFirestore, collection, addDoc, doc, setDoc, onSnapshot, serverTimestamp,
  query, orderBy, limit,
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore.js";
import {
  getAuth, GoogleAuthProvider, onAuthStateChanged, signInWithPopup, signOut,
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js";
import { firebaseConfig } from "./firebase-config.js?v=20261030135055";
import {
  THEMES, applyTheme, currentTheme, currentThemeKey,
} from "./theme.js?v=20261030135055";

// Stamped by scripts/stamp-version.sh; must match docs/version.json when deployed.
const APP_VERSION = "20261030135055";

// Typing this into the link field opens the admin panel. It only reveals the
// panel; actually using it requires a Google account with the admin claim.
const ADMIN_CODE = "admin";

const app = initializeApp(firebaseConfig);
const db = getFirestore(app);
const auth = getAuth(app);
const stateRef = doc(db, "config", "state");

// Which service a link is from. Keep in sync with firestore.rules; the helper
// decides whether it's a single song.
const PLATFORMS = {
  youtube: /^https:\/\/(www\.|m\.|music\.)?(youtube\.com|youtu\.be)\/\S+$/,
  apple: /^https:\/\/music\.apple\.com\/\S+$/,
  soundcloud: /^https:\/\/(www\.|m\.|on\.)?soundcloud\.com\/\S+$/,
  spotify: /^https:\/\/(open\.spotify\.com|spotify\.link)\/\S+$/,
};

// Where each status puts the tracker. The wording comes from the theme.
const STATUS_STEP = { pending: 0, downloading: 1, done: 2, failed: 1 };
const closedText = () => `${currentTheme().text.closedTitle} ${currentTheme().text.closedBody}`;

const $ = (id) => document.getElementById(id);
const form = $("request-form");
const urlInput = $("url");
const requesterInput = $("requester");
const submitBtn = $("submit");
const statusEl = $("status");
const tracker = $("tracker");
const closedBanner = $("closed-banner");
const launchIcon = document.querySelector(".launch-icon");

let accepting = true;
// True while signed in with the admin claim: requests are marked as DJ
// requests, which the helper lets be playlists or albums.
let isDj = false;
let unsubscribeRequest = null;
// Kept so the page can be redrawn in the new wording when the theme changes.
let lastStatus = null;
let lastBoard = null;

// ---------- Stay up to date ----------
// GitHub Pages lets browsers cache files for 10 minutes, so a tab (or a cached
// copy of the page) can be running old code after a deploy. Check the live
// version and reload onto it when it changes.

const params = new URLSearchParams(location.search);
if (params.has("v")) history.replaceState(null, "", location.pathname + location.hash);

async function checkForUpdate() {
  try {
    const res = await fetch(`version.json?t=${Date.now()}`, { cache: "no-store" });
    const { version } = await res.json();
    const busy = urlInput.value.trim() !== "" || !adminPanel.hidden;
    // The ?v= guard stops a reload loop if the CDN is still serving old files.
    if (version && version !== APP_VERSION && !busy && params.get("v") !== version) {
      location.replace(`${location.pathname}?v=${version}`);
    }
  } catch {
    // Offline or version.json missing; try again next time.
  }
}

// ---------- Guest form ----------

function setStatus(text, kind = "") {
  statusEl.textContent = text;
  statusEl.className = `status ${kind}`;
}

function detectPlatform(url) {
  return Object.keys(PLATFORMS).find((name) => PLATFORMS[name].test(url)) ?? null;
}

function showProgress(status) {
  if (!(status in STATUS_STEP)) return;
  lastStatus = status;
  const failed = status === "failed";
  tracker.hidden = false;
  tracker.dataset.step = STATUS_STEP[status];
  tracker.classList.toggle("failed", failed);
  setStatus(currentTheme().status[status], failed ? "error" : "ok");
}

function takeoff() {
  launchIcon.classList.remove("go");
  void launchIcon.offsetWidth; // restart the animation
  launchIcon.classList.add("go");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = urlInput.value.trim();

  if (url.toLowerCase() === ADMIN_CODE) {
    form.reset();
    openAdmin();
    return;
  }
  if (!accepting) {
    setStatus(closedText(), "error");
    return;
  }
  const platform = detectPlatform(url);
  if (!platform) {
    setStatus(currentTheme().text.badLink, "error");
    return;
  }

  const payload = { url, platform, status: "pending", createdAt: serverTimestamp() };
  const requester = requesterInput.value.trim();
  if (requester) payload.requester = requester;
  if (isDj) payload.dj = true;

  submitBtn.disabled = true;
  try {
    const ref = await addDoc(collection(db, "requests"), payload);
    urlInput.value = "";
    takeoff();
    showProgress("pending");
    unsubscribeRequest?.();
    unsubscribeRequest = onSnapshot(doc(db, "requests", ref.id), (snap) => {
      showProgress(snap.data()?.status);
    });
  } catch (err) {
    console.error(err);
    setStatus(err.code === "permission-denied" && !accepting
      ? closedText()
      : currentTheme().text.sendFailed, "error");
  } finally {
    submitBtn.disabled = false;
  }
});

// ---------- Shared state: open / paused, and the theme ----------

function renderAccepting() {
  const { text } = currentTheme();
  closedBanner.hidden = accepting;
  form.classList.toggle("closed", !accepting);
  submitBtn.textContent = accepting ? text.submit : text.submitClosed;

  document.querySelector(".switch").dataset.open = accepting;
  $("switch-state").textContent = accepting ? text.open : text.closed;
  $("toggle").textContent = accepting ? text.closeButton : text.openButton;
}

// Redraw everything whose wording comes from the theme.
function renderAll() {
  renderAccepting();
  setDj(isDj);
  if (lastStatus) showProgress(lastStatus);
  if (lastBoard) renderBoard(lastBoard);
  themeSelect.value = currentThemeKey();
}

const themeSelect = $("theme-select");
for (const [key, theme] of Object.entries(THEMES)) {
  themeSelect.add(new Option(theme.name, key));
}
renderAll();

onSnapshot(stateRef, (snap) => {
  const data = snap.exists() ? snap.data() : {};
  accepting = data.accepting !== false;
  applyTheme(data.theme);
  renderAll();
}, (err) => console.error("state listener", err));

// Merge, so pausing doesn't reset the theme and vice versa. `accepting` is
// always sent because the rules require it.
function saveState(fields) {
  return setDoc(stateRef, {
    accepting, ...fields, updatedAt: serverTimestamp(), updatedBy: "admin",
  }, { merge: true });
}

// ---------- Admin panel ----------

const guest = $("guest");
const adminPanel = $("admin");

checkForUpdate();
setInterval(checkForUpdate, 60_000);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") checkForUpdate();
});
const adminMsg = $("admin-msg");
let unsubscribeBoard = null;

function openAdmin() {
  guest.hidden = true;
  adminPanel.hidden = false;
}

$("admin-exit").addEventListener("click", () => {
  adminPanel.hidden = true;
  guest.hidden = false;
});

$("sign-in").addEventListener("click", () => {
  adminMsg.textContent = "";
  signInWithPopup(auth, new GoogleAuthProvider()).catch((err) => {
    adminMsg.textContent = `Sign-in failed: ${err.message}`;
  });
});

$("sign-out").addEventListener("click", () => signOut(auth));

$("toggle").addEventListener("click", async (event) => {
  event.target.disabled = true;
  try {
    await saveState({ accepting: !accepting });
  } catch (err) {
    adminMsg.textContent = `Couldn't change requests: ${err.message}`;
  } finally {
    event.target.disabled = false;
  }
});

themeSelect.addEventListener("change", async () => {
  themeSelect.disabled = true;
  try {
    await saveState({ theme: themeSelect.value });
  } catch (err) {
    adminMsg.textContent = `Couldn't change theme: ${err.message}`;
    themeSelect.value = currentThemeKey();
  } finally {
    themeSelect.disabled = false;
  }
});

function setDj(on) {
  isDj = on;
  const { text } = currentTheme();
  $("pass-type").textContent = on ? text.djPass : text.pass;
}

onAuthStateChanged(auth, async (user) => {
  setDj(false);
  unsubscribeBoard?.();
  unsubscribeBoard = null;
  lastBoard = null;
  $("sign-out").hidden = !user;
  $("admin-auth").hidden = Boolean(user);
  $("admin-controls").hidden = true;
  adminMsg.textContent = "";
  if (!user) return;

  // Force a refresh so a freshly granted admin claim is picked up.
  const token = await user.getIdTokenResult(true);
  setDj(Boolean(token.claims.admin));
  if (!token.claims.admin) {
    adminMsg.innerHTML = "Signed in as <b></b>, but this account isn't an admin. " +
      "On the laptop, run <code></code> then reload.";
    adminMsg.querySelector("b").textContent = user.email;
    adminMsg.querySelector("code").textContent =
      `python helper/dj_helper.py --grant-admin ${user.email}`;
    return;
  }

  $("admin-controls").hidden = false;
  const recent = query(collection(db, "requests"), orderBy("createdAt", "desc"), limit(25));
  unsubscribeBoard = onSnapshot(recent, renderBoard, (err) => {
    adminMsg.textContent = `Request list unavailable: ${err.message}`;
  });
});

function renderBoard(snap) {
  lastBoard = snap;
  const { text, board: labels } = currentTheme();
  const board = $("board");
  board.replaceChildren();
  if (snap.empty) {
    const row = board.insertRow();
    const cell = row.insertCell();
    cell.colSpan = 4;
    cell.className = "empty";
    cell.textContent = text.boardEmpty;
    return;
  }
  for (const docSnap of snap.docs) {
    const data = docSnap.data();
    const row = board.insertRow();
    const time = data.createdAt?.toDate();
    row.insertCell().textContent = time
      ? time.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : "--:--";
    row.insertCell().textContent = data.requester || "—";
    const status = row.insertCell();
    status.className = `st-${data.status}`;
    status.textContent = labels[data.status] ?? data.status;
    const track = row.insertCell();
    track.className = "track-cell";
    track.textContent = data.files?.[0]?.replace(/\.aiff$/, "") || data.url;
    track.title = data.error || data.url;
  }
}
