// Look and feel for each kind of gig. The admin panel picks one; it's saved in
// Firestore (config/state.theme), so every open guest page switches live.
//
// To add a theme: add an entry to THEMES, then a [data-theme="<key>"] block in
// style.css with its colours and any scene styles.

export const DEFAULT_THEME = "party";

const JET = "M48 12 L38 10.4 L28 10 L20 1 L15 1 L19 10 L8 10.4 L3 5 L0 5 L2 12 L0 19 L3 19 L8 13.6 L19 14 L15 23 L20 23 L28 14 L38 13.6 Z";
const NOTE = "M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z";

const GOLDEN_GATE = `
  <div class="stars"></div>
  <div class="formation">
    <div class="plane lead"><span class="trail short"></span><svg class="jet"><use href="#icon"/></svg></div>
    <div class="plane left"><span class="trail"></span><svg class="jet"><use href="#icon"/></svg></div>
    <div class="plane right"><span class="trail"></span><svg class="jet"><use href="#icon"/></svg></div>
    <div class="plane slot"><span class="trail"></span><svg class="jet"><use href="#icon"/></svg></div>
  </div>
  <svg class="bridge" viewBox="0 0 1000 140" preserveAspectRatio="xMidYMax meet">
    <path class="hangers" d="M144 96V100 M170 90V100 M194 81V100 M215 70V100 M234 56V100 M275 51V100 M300 60V100 M325 69V100 M350 76V100 M375 82V100 M400 87V100 M425 91V100 M450 94V100 M475 95V100 M500 96V100 M525 95V100 M550 94V100 M575 91V100 M600 87V100 M625 82V100 M650 76V100 M675 69V100 M700 60V100 M725 51V100 M766 56V100 M785 70V100 M806 81V100 M830 90V100 M856 96V100"/>
    <path class="cable" d="M115 100 Q205 92 250 40 Q500 152 750 40 Q795 92 885 100"/>
    <path class="pylon" d="M243 140 L245 34 L255 34 L257 140 Z"/>
    <path class="pylon" d="M743 140 L745 34 L755 34 L757 140 Z"/>
    <rect class="deck" x="0" y="100" width="1000" height="5"/>
    <rect class="water" x="0" y="116" width="1000" height="24"/>
  </svg>`;

// Equalizer bars with staggered speeds so they don't move in lockstep.
const DANCE_FLOOR = `
  <div class="glow one"></div>
  <div class="glow two"></div>
  <div class="eq">${Array.from({ length: 28 }, (_, i) =>
    `<span style="--speed:${0.7 + ((i * 37) % 10) / 10}s;--delay:-${((i * 53) % 10) / 10}s"></span>`).join("")}</div>`;

export const THEMES = {
  party: {
    name: "Party (generic)",
    title: "Request a Song",
    color: "#120a24",
    favicon: "favicon-party.svg",
    icon: { viewBox: "0 0 24 24", path: NOTE },
    scene: DANCE_FLOOR,
    text: {
      eyebrow: "Live · DJ Requests",
      heading: "Request a",
      headingAccent: "Song",
      lede: "Paste a song link from Spotify, Apple Music, YouTube or SoundCloud and we'll queue it up.",
      pass: "Request",
      djPass: "DJ pass",
      routeFrom: "Floor",
      routeTo: "Decks",
      urlLabel: "Song",
      urlHint: "link",
      nameLabel: "Name",
      nameHint: "optional",
      namePlaceholder: "Your name",
      submit: "Send it",
      submitClosed: "Requests paused",
      closedTitle: "Requests paused.",
      closedBody: "The DJ isn't taking requests right now. Check back soon.",
      stops: ["Queued", "Downloading", "Ready"],
      badLink: "That link won't work. Use a YouTube, Spotify, Apple Music or SoundCloud link.",
      sendFailed: "Couldn't send your request. Try again.",
      adminTitle: "DJ Booth",
      switchLabel: "Requests",
      open: "OPEN",
      closed: "PAUSED",
      openButton: "Open requests",
      closeButton: "Pause requests",
      boardTitle: "Recent requests",
      boardEmpty: "No requests yet",
    },
    status: {
      pending: "Got it. You're in the queue.",
      downloading: "Downloading your track now.",
      done: "It's in the DJ's crate. 🎶",
      failed: "That one didn't work. Try another link?",
    },
    board: { pending: "QUEUED", downloading: "LOADING", done: "READY", failed: "FAILED" },
  },

  fleetweek: {
    name: "SF Fleet Week",
    title: "Fleet Week Requests",
    color: "#0a1633",
    favicon: "favicon.svg",
    icon: { viewBox: "0 0 48 24", path: JET },
    scene: GOLDEN_GATE,
    text: {
      eyebrow: "SF Fleet Week · DJ Control",
      heading: "Request a",
      headingAccent: "Song",
      lede: "Paste a song link from Spotify, Apple Music, YouTube or SoundCloud and we'll get it airborne.",
      pass: "Boarding pass",
      djPass: "Captain's pass",
      routeFrom: "Crowd",
      routeTo: "Decks",
      urlLabel: "Flight plan",
      urlHint: "song link",
      nameLabel: "Callsign",
      nameHint: "your name, optional",
      namePlaceholder: "Maverick",
      submit: "Cleared for takeoff",
      submitClosed: "Airspace closed",
      closedTitle: "Airspace closed.",
      closedBody: "The DJ isn't taking requests right now. Check back soon.",
      stops: ["Tower", "In flight", "Landed"],
      badLink: "That flight plan won't fly. Use a YouTube, Spotify, Apple Music or SoundCloud link.",
      sendFailed: "Radio trouble. Couldn't send your request, try again.",
      adminTitle: "Control Tower",
      switchLabel: "Airspace",
      open: "OPEN",
      closed: "CLOSED",
      openButton: "Open airspace",
      closeButton: "Close airspace",
      boardTitle: "Departures",
      boardEmpty: "No flights yet",
    },
    status: {
      pending: "Tower copies. You're in the holding pattern.",
      downloading: "Wheels up! Your track is inbound.",
      done: "Touchdown. Your song landed in the DJ's hangar. 🎶",
      failed: "Mayday! That track couldn't land. Try another link?",
    },
    board: { pending: "HOLDING", downloading: "INBOUND", done: "LANDED", failed: "MAYDAY" },
  },
};

const CACHE_KEY = "dj-request-theme";
let currentKey = null;

export function currentTheme() {
  return THEMES[currentKey] ?? THEMES[DEFAULT_THEME];
}

export function currentThemeKey() {
  return currentKey;
}

// Applies a theme to the page. Returns true if it changed anything.
export function applyTheme(key) {
  if (!THEMES[key]) key = DEFAULT_THEME;
  if (key === currentKey) return false;
  currentKey = key;
  const theme = THEMES[key];

  document.documentElement.dataset.theme = key;
  document.title = theme.title;
  document.querySelector('meta[name="theme-color"]').content = theme.color;
  document.querySelector('link[rel="icon"][type="image/svg+xml"]').href = theme.favicon;

  const icon = document.getElementById("icon");
  icon.setAttribute("viewBox", theme.icon.viewBox);
  icon.querySelector("path").setAttribute("d", theme.icon.path);
  document.querySelector(".scene").innerHTML = theme.scene;

  for (const el of document.querySelectorAll("[data-text]")) {
    el.textContent = theme.text[el.dataset.text];
  }
  for (const el of document.querySelectorAll("[data-placeholder]")) {
    el.placeholder = theme.text[el.dataset.placeholder];
  }
  document.querySelectorAll(".stops li").forEach((li, i) => {
    li.textContent = theme.text.stops[i];
  });

  try { localStorage.setItem(CACHE_KEY, key); } catch { /* storage blocked */ }
  return true;
}

// Use the last theme this browser saw, so the page doesn't flash the default
// while Firestore loads.
let cached = null;
try { cached = localStorage.getItem(CACHE_KEY); } catch { /* storage blocked */ }
applyTheme(cached);
