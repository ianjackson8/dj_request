import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import {
  getFirestore, collection, addDoc, doc, setDoc, onSnapshot, serverTimestamp,
  query, orderBy, limit,
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore.js";
import {
  getAuth, GoogleAuthProvider, onAuthStateChanged, signInWithPopup, signOut,
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js";
import { firebaseConfig } from "./firebase-config.js";

// Typing this into the link field opens the control tower. It only reveals the
// panel; actually using it requires a Google account with the admin claim.
const TOWER_CODE = "tower";

const app = initializeApp(firebaseConfig);
const db = getFirestore(app);
const auth = getAuth(app);
const stateRef = doc(db, "config", "state");

// Keep in sync with helper/dj_helper.py and firestore.rules.
const PLATFORMS = {
  youtube: /^https:\/\/(www\.|m\.|music\.)?(youtube\.com|youtu\.be)\/\S+$/,
  apple: /^https:\/\/music\.apple\.com\/\S*(\/song\/|[?&]i=\d+)\S*$/,
};

const STATUS = {
  pending: { step: 0, text: "Tower copies. You're in the holding pattern." },
  downloading: { step: 1, text: "Wheels up! Your track is inbound." },
  done: { step: 2, text: "Touchdown. Your song landed in the DJ's hangar. 🎶" },
  failed: { step: 1, text: "Mayday! That track couldn't land. Try another link?", error: true },
};
const CLOSED_TEXT = "Airspace closed. The DJ isn't taking requests right now.";

const $ = (id) => document.getElementById(id);
const form = $("request-form");
const urlInput = $("url");
const requesterInput = $("requester");
const submitBtn = $("submit");
const statusEl = $("status");
const tracker = $("tracker");
const closedBanner = $("closed-banner");
const launchJet = document.querySelector(".launch-jet");

let accepting = true;
let unsubscribeRequest = null;

// ---------- Guest form ----------

function setStatus(text, kind = "") {
  statusEl.textContent = text;
  statusEl.className = `status ${kind}`;
}

function detectPlatform(url) {
  return Object.keys(PLATFORMS).find((name) => PLATFORMS[name].test(url)) ?? null;
}

function showProgress(status) {
  const info = STATUS[status];
  if (!info) return;
  tracker.hidden = false;
  tracker.dataset.step = info.step;
  tracker.classList.toggle("failed", Boolean(info.error));
  setStatus(info.text, info.error ? "error" : "ok");
}

function takeoff() {
  launchJet.classList.remove("go");
  void launchJet.offsetWidth; // restart the animation
  launchJet.classList.add("go");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = urlInput.value.trim();

  if (url.toLowerCase() === TOWER_CODE) {
    form.reset();
    openTower();
    return;
  }
  if (!accepting) {
    setStatus(CLOSED_TEXT, "error");
    return;
  }
  const platform = detectPlatform(url);
  if (!platform) {
    setStatus("That flight plan won't fly. Use a YouTube or Apple Music song link.", "error");
    return;
  }

  const payload = { url, platform, status: "pending", createdAt: serverTimestamp() };
  const requester = requesterInput.value.trim();
  if (requester) payload.requester = requester;

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
      ? CLOSED_TEXT
      : "Radio trouble. Couldn't send your request, try again.", "error");
  } finally {
    submitBtn.disabled = false;
  }
});

// ---------- Airspace (pause / resume) ----------

onSnapshot(stateRef, (snap) => {
  accepting = snap.exists() ? snap.data().accepting !== false : true;
  closedBanner.hidden = accepting;
  form.classList.toggle("closed", !accepting);
  submitBtn.textContent = accepting ? "Cleared for takeoff" : "Airspace closed";

  const airspace = document.querySelector(".airspace");
  airspace.dataset.open = accepting;
  $("airspace-state").textContent = accepting ? "OPEN" : "CLOSED";
  $("toggle").textContent = accepting ? "Close airspace" : "Open airspace";
}, (err) => console.error("airspace listener", err));

// ---------- Control tower ----------

const guest = $("guest");
const tower = $("tower");
const towerMsg = $("tower-msg");
let unsubscribeBoard = null;

function openTower() {
  guest.hidden = true;
  tower.hidden = false;
}

$("tower-exit").addEventListener("click", () => {
  tower.hidden = true;
  guest.hidden = false;
});

$("sign-in").addEventListener("click", () => {
  towerMsg.textContent = "";
  signInWithPopup(auth, new GoogleAuthProvider()).catch((err) => {
    towerMsg.textContent = `Sign-in failed: ${err.message}`;
  });
});

$("sign-out").addEventListener("click", () => signOut(auth));

$("toggle").addEventListener("click", async (event) => {
  event.target.disabled = true;
  try {
    await setDoc(stateRef, {
      accepting: !accepting, updatedAt: serverTimestamp(), updatedBy: "tower",
    });
  } catch (err) {
    towerMsg.textContent = `Couldn't change airspace: ${err.message}`;
  } finally {
    event.target.disabled = false;
  }
});

onAuthStateChanged(auth, async (user) => {
  unsubscribeBoard?.();
  unsubscribeBoard = null;
  $("sign-out").hidden = !user;
  $("tower-auth").hidden = Boolean(user);
  $("tower-controls").hidden = true;
  towerMsg.textContent = "";
  if (!user) return;

  // Force a refresh so a freshly granted admin claim is picked up.
  const token = await user.getIdTokenResult(true);
  if (!token.claims.admin) {
    towerMsg.innerHTML = "Signed in as <b></b>, but you're not cleared for the tower. " +
      "On the laptop, run <code></code> then reload.";
    towerMsg.querySelector("b").textContent = user.email;
    towerMsg.querySelector("code").textContent =
      `python helper/dj_helper.py --grant-admin ${user.email}`;
    return;
  }

  $("tower-controls").hidden = false;
  const recent = query(collection(db, "requests"), orderBy("createdAt", "desc"), limit(25));
  unsubscribeBoard = onSnapshot(recent, renderBoard, (err) => {
    towerMsg.textContent = `Departures board unavailable: ${err.message}`;
  });
});

function renderBoard(snap) {
  const board = $("board");
  board.replaceChildren();
  if (snap.empty) {
    const row = board.insertRow();
    const cell = row.insertCell();
    cell.colSpan = 4;
    cell.className = "empty";
    cell.textContent = "No flights yet";
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
    status.textContent = { pending: "HOLDING", downloading: "INBOUND", done: "LANDED", failed: "MAYDAY" }[data.status] ?? data.status;
    const track = row.insertCell();
    track.className = "track-cell";
    track.textContent = data.files?.[0]?.replace(/\.aiff$/, "") || data.url;
    track.title = data.error || data.url;
  }
}
