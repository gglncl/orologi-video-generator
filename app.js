const $ = (id) => document.getElementById(id);

const els = {
  script: $("script"), hook: $("hook"), voice: $("voice"), rate: $("rate"),
  outro: $("outro"), visuals: $("visuals"), charCount: $("charCount"),
  generateBtn: $("generateBtn"), testVoiceBtn: $("testVoiceBtn"),
  statusCard: $("statusCard"), statusTitle: $("statusTitle"), statusText: $("statusText"),
  statusBadge: $("statusBadge"), progressBar: $("progressBar"), runLink: $("runLink"),
  voiceResult: $("voiceResult"), voiceAudio: $("voiceAudio"),
  videoResult: $("videoResult"), videoPreview: $("videoPreview"), downloadVideo: $("downloadVideo"),
  settingsBtn: $("settingsBtn"), settingsDialog: $("settingsDialog"),
  workerUrl: $("workerUrl"), appPin: $("appPin"), saveSettings: $("saveSettings")
};

const DEFAULT_VOICE = "it-IT-GiuseppeMultilingualNeural";

const store = {
  get(key, fallback = "") { return localStorage.getItem(key) ?? fallback; },
  set(key, value) { localStorage.setItem(key, value); }
};

function basePath(file) {
  return new URL(file, window.location.href).toString();
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function loadPrefs() {
  els.workerUrl.value = store.get("ovg_worker_url");
  els.appPin.value = store.get("ovg_app_pin");

  const savedVoice = store.get("ovg_voice", DEFAULT_VOICE);
  const availableVoices = [...els.voice.options].map(o => o.value);
  els.voice.value = availableVoices.includes(savedVoice) ? savedVoice : DEFAULT_VOICE;

  els.rate.value = store.get("ovg_rate", "+6%");
  els.outro.value = store.get("ovg_outro", "OROLOGI SPIEGATI SEMPLICE ⌚");
  els.script.value = store.get("ovg_script");
  els.hook.value = store.get("ovg_hook");
  updateChars();
}

function saveDraft() {
  store.set("ovg_voice", els.voice.value || DEFAULT_VOICE);
  store.set("ovg_rate", els.rate.value);
  store.set("ovg_outro", els.outro.value);
  store.set("ovg_script", els.script.value);
  store.set("ovg_hook", els.hook.value);
}

function updateChars() {
  els.charCount.textContent = `${els.script.value.length} caratteri`;
}

function ensureSettings() {
  const worker = store.get("ovg_worker_url").replace(/\/$/, "");
  const pin = store.get("ovg_app_pin");
  if (!worker || !pin) {
    els.settingsDialog.showModal();
    throw new Error("Configura prima Worker URL e PIN.");
  }
  return { worker, pin };
}

function setBusy(busy) {
  els.generateBtn.disabled = busy;
  els.testVoiceBtn.disabled = busy;
}

function showStatus(title, text, pct, badge = "IN CORSO") {
  els.statusCard.classList.remove("hidden");
  els.statusTitle.textContent = title;
  els.statusText.textContent = text;
  els.statusBadge.textContent = badge;
  els.progressBar.style.width = `${pct}%`;
}

function hideResults() {
  els.voiceResult.classList.add("hidden");
  els.videoResult.classList.add("hidden");
  els.runLink.classList.add("hidden");
  els.voiceAudio.removeAttribute("src");
  els.videoPreview.removeAttribute("src");
  els.voiceAudio.load();
  els.videoPreview.load();
}

async function api(path, options = {}) {
  const { worker, pin } = ensureSettings();
  const res = await fetch(`${worker}${path}`, {
    ...options,
    headers: {
      "content-type": "application/json",
      "x-app-pin": pin,
      ...(options.headers || {})
    }
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Errore ${res.status}`);
  return data;
}

async function waitForDeployment(requestId, mode) {
  const deadline = Date.now() + 90000;

  while (Date.now() < deadline) {
    try {
      const url = `${basePath("deploy.json")}?request=${encodeURIComponent(requestId)}&t=${Date.now()}`;
      const res = await fetch(url, { cache: "no-store" });

      if (res.ok) {
        const info = await res.json();
        if (
          info.request_id === requestId &&
          info.mode === mode &&
          info.deployed === true
        ) {
          return;
        }
      }
    } catch (_) {}

    await sleep(1500);
  }

  throw new Error("Il motore ha finito, ma la nuova versione non è ancora pubblicata.");
}

async function waitForMedia(filename) {
  const deadline = Date.now() + 30000;

  while (Date.now() < deadline) {
    const url = `${basePath(filename)}?t=${Date.now()}`;
    try {
      const res = await fetch(url, { method: "HEAD", cache: "no-store" });
      if (res.ok) return url;
    } catch (_) {}
    await sleep(1000);
  }

  throw new Error("Il file è stato generato ma non è ancora disponibile sul sito.");
}

async function start(mode) {
  const script = els.script.value.trim();
  if (mode !== "deploy_only" && !script) throw new Error("Incolla prima lo script.");

  saveDraft();
  hideResults();
  showStatus("Invio richiesta…", "Il motore sta partendo.", 8, "AVVIO");

  const data = await api("/api/generate", {
    method: "POST",
    body: JSON.stringify({
      mode,
      script,
      hook: els.hook.value.trim(),
      voice: els.voice.value || DEFAULT_VOICE,
      rate: els.rate.value,
      outro: els.outro.value.trim(),
      visual_queries: els.visuals.value.trim()
    })
  });

  await poll(data.request_id, mode);
}

async function poll(requestId, mode) {
  const started = Date.now();

  while (Date.now() - started < 12 * 60 * 1000) {
    await sleep(4000);
    const s = await api(`/api/status?id=${encodeURIComponent(requestId)}`);

    if (s.run_url) {
      els.runLink.href = s.run_url;
      els.runLink.classList.remove("hidden");
    }

    if (s.status === "not_found" || s.status === "queued") {
      showStatus("In coda…", "GitHub sta preparando il motore.", 18, "CODA");
      continue;
    }

    if (s.status === "in_progress") {
      const elapsed = Math.round((Date.now() - started) / 1000);
      const pct = mode === "generate_video"
        ? Math.min(86, 28 + elapsed * 0.45)
        : Math.min(86, 35 + elapsed * 1.1);

      showStatus(
        mode === "generate_video" ? "Sto creando il video…" : "Sto creando la voce…",
        mode === "generate_video"
          ? "Voce, clip, sottotitoli e montaggio automatico."
          : "Genero una breve anteprima.",
        pct,
        "IN CORSO"
      );
      continue;
    }

    if (s.status === "completed" && s.conclusion === "success") {
      showStatus(
        "Pubblicazione…",
        "Il motore ha finito. Aspetto la versione esatta di questa richiesta.",
        94,
        "PUBBLICAZIONE"
      );

      await waitForDeployment(requestId, mode);

      if (mode === "test_voice") {
        const filename = `voice_test_${requestId}.mp3`;
        const url = await waitForMedia(filename);
        els.voiceAudio.src = url;
        els.voiceResult.classList.remove("hidden");
        els.voiceAudio.load();
      } else if (mode === "generate_video") {
        const filename = `orologi_video_${requestId}.mp4`;
        const url = await waitForMedia(filename);
        els.videoPreview.src = url;
        els.downloadVideo.href = url;
        els.videoResult.classList.remove("hidden");
        els.videoPreview.load();
      }

      showStatus("Pronto ✓", "Nuovo risultato pubblicato.", 100, "COMPLETATO");
      return;
    }

    if (s.status === "completed") {
      const msg = mode === "test_voice"
        ? "La sintesi vocale non ha restituito audio."
        : "La generazione non è riuscita. Apri i dettagli tecnici per vedere il punto esatto.";
      showStatus("Generazione fallita", msg, 100, "ERRORE");
      throw new Error(msg);
    }
  }

  throw new Error("Tempo massimo superato.");
}

els.script.addEventListener("input", () => {
  updateChars();
  saveDraft();
});
els.hook.addEventListener("input", saveDraft);
[els.voice, els.rate, els.outro].forEach(el => el.addEventListener("change", saveDraft));

els.settingsBtn.addEventListener("click", () => els.settingsDialog.showModal());

els.saveSettings.addEventListener("click", (event) => {
  event.preventDefault();
  const worker = els.workerUrl.value.trim().replace(/\/$/, "");
  const pin = els.appPin.value.trim();

  if (!worker.startsWith("https://") || pin.length < 4) {
    alert("Inserisci un Worker URL https valido e un PIN di almeno 4 caratteri.");
    return;
  }

  store.set("ovg_worker_url", worker);
  store.set("ovg_app_pin", pin);
  els.settingsDialog.close();
});

els.testVoiceBtn.addEventListener("click", async () => {
  setBusy(true);
  try {
    await start("test_voice");
  } catch (e) {
    showStatus("Errore", e.message, 100, "ERRORE");
  } finally {
    setBusy(false);
  }
});

els.generateBtn.addEventListener("click", async () => {
  setBusy(true);
  try {
    await start("generate_video");
  } catch (e) {
    showStatus("Errore", e.message, 100, "ERRORE");
  } finally {
    setBusy(false);
  }
});

loadPrefs();

if (!store.get("ovg_worker_url") || !store.get("ovg_app_pin")) {
  setTimeout(() => els.settingsDialog.showModal(), 350);
}
