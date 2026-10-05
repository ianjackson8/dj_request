import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import {
  getFirestore, collection, addDoc, doc, onSnapshot, serverTimestamp,
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore.js";
import { firebaseConfig } from "./firebase-config.js";

const db = getFirestore(initializeApp(firebaseConfig));

// Keep in sync with helper/dj_helper.py and firestore.rules.
const SERVICES = {
  youtube: /^https:\/\/(www\.|m\.|music\.)?(youtube\.com|youtu\.be)\/\S+$/,
  apple: /^https:\/\/music\.apple\.com\/\S*(\/song\/|[?&]i=\d+)\S*$/,
};

const STATUS_TEXT = {
  pending: "Sent! Waiting for the DJ…",
  downloading: "The DJ is grabbing your song…",
  done: "Got it — it's in the DJ's crate. 🎶",
  failed: "Couldn't download that one. Try a different link?",
};

const form = document.getElementById("request-form");
const urlInput = document.getElementById("url");
const requesterInput = document.getElementById("requester");
const submitBtn = document.getElementById("submit");
const statusEl = document.getElementById("status");

let unsubscribe = null;

function setStatus(text, kind = "") {
  statusEl.textContent = text;
  statusEl.className = kind;
}

function detectService(url) {
  return Object.keys(SERVICES).find((name) => SERVICES[name].test(url)) ?? null;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = urlInput.value.trim();
  const service = detectService(url);
  if (!service) {
    setStatus("That link isn't supported. Use a YouTube or Apple Music song link.", "error");
    return;
  }

  const payload = { url, platform: service, status: "pending", createdAt: serverTimestamp() };
  const requester = requesterInput.value.trim();
  if (requester) payload.requester = requester;

  submitBtn.disabled = true;
  try {
    const ref = await addDoc(collection(db, "requests"), payload);
    form.reset();
    setStatus(STATUS_TEXT.pending, "ok");
    unsubscribe?.();
    unsubscribe = onSnapshot(doc(db, "requests", ref.id), (snap) => {
      const status = snap.data()?.status;
      if (status) setStatus(STATUS_TEXT[status], status === "failed" ? "error" : "ok");
    });
  } catch (err) {
    console.error(err);
    setStatus("Something went wrong sending your request.", "error");
  } finally {
    submitBtn.disabled = false;
  }
});
