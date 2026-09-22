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

const store = {
  get(key, fallback = "") { return localStorage.getItem(key) ?? fallback; },
  set(key, value) { localStorage.setItem(key, value); }
};

function basePath(file) {
  return new URL(file, window.location.href).toString();
}

function loadPrefs() {
  els.workerUrl.value = store.get("ovg_worker_url");
  els.appPin.value = store.get("ovg_app_pin");
  els.voice.value = store.get("ovg_voice", "it-IT-DiegoNeural");
  els.rate.value = store.get("ovg_rate", "+6%");
  els.outro.value = store.get("ovg_outro", "OROLOGI SPIEGATI SEMPLICE ⌚");
  els.script.value = store.get("ovg_script");
  els.hook.value = store.get("ovg_hook");
  updateChars();
}

function saveDraft() {
  store.set("ovg_voice", els.voice.value);
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
      voice: els.voice.value,
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
    await new Promise(r => setTimeout(r, 4000));
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
      const pct = mode === "generate_video" ? Math.min(84, 28 + elapsed * 0.45) : Math.min(84, 35 + elapsed * 1.1);
      showStatus(mode === "generate_video" ? "Sto creando il video…" : "Sto creando la voce…",
        mode === "generate_video" ? "Voce, clip, sottotitoli e montaggio automatico." : "Genero una breve anteprima.",
        pct, "IN CORSO");
      continue;
    }
    if (s.status === "completed" && s.conclusion === "success") {
      showStatus("Pronto ✓", "Risultato pubblicato.", 100, "COMPLETATO");
      const cache = `?v=${encodeURIComponent(requestId)}&t=${Date.now()}`;
      if (mode === "test_voice") {
        els.voiceAudio.src = basePath("voice_test.mp3") + cache;
        els.voiceResult.classList.remove("hidden");
        els.voiceAudio.load();
      } else if (mode === "generate_video") {
        const url = basePath("orologi_video.mp4") + cache;
        els.videoPreview.src = url;
        els.downloadVideo.href = url;
        els.videoResult.classList.remove("hidden");
        els.videoPreview.load();
      }
      return;
    }
    if (s.status === "completed") {
      showStatus("Generazione fallita", "Apri i dettagli tecnici per vedere il punto esatto.", 100, "ERRORE");
      throw new Error("Il motore ha terminato con un errore.");
    }
  }
  throw new Error("Tempo massimo superato.");
}

els.script.addEventListener("input", updateChars);
[els.script, els.hook, els.voice, els.rate, els.outro].forEach(el => el.addEventListener("change", saveDraft));
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
  try { await start("test_voice"); }
  catch (e) { showStatus("Errore", e.message, 100, "ERRORE"); }
  finally { setBusy(false); }
});

els.generateBtn.addEventListener("click", async () => {
  setBusy(true);
  try { await start("generate_video"); }
  catch (e) { showStatus("Errore", e.message, 100, "ERRORE"); }
  finally { setBusy(false); }
});

loadPrefs();
if (!store.get("ovg_worker_url") || !store.get("ovg_app_pin")) {
  setTimeout(() => els.settingsDialog.showModal(), 350);
}
