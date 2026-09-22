const OWNER = "gglncl";
const REPO = "orologi-video-generator";
const WORKFLOW = "generate-watch-video.yml";
const REF = "main";

function cors() {
  return {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET,POST,OPTIONS",
    "access-control-allow-headers": "content-type,x-app-pin"
  };
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...cors() }
  });
}

async function github(env, path, options = {}) {
  const res = await fetch(`https://api.github.com${path}`, {
    ...options,
    headers: {
      "accept": "application/vnd.github+json",
      "authorization": `Bearer ${env.GITHUB_TOKEN}`,
      "x-github-api-version": "2022-11-28",
      "user-agent": "orologi-video-generator-worker",
      ...(options.headers || {})
    }
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`GitHub ${res.status}: ${text.slice(0, 500)}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

function authorized(request, env) {
  const pin = request.headers.get("x-app-pin") || "";
  return env.APP_PIN && pin === env.APP_PIN;
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") return new Response(null, { headers: cors() });
    if (!authorized(request, env)) return json({ error: "PIN non valido" }, 401);

    const url = new URL(request.url);

    try {
      if (url.pathname === "/api/generate" && request.method === "POST") {
        const body = await request.json();
        const mode = ["deploy_only", "test_voice", "generate_video"].includes(body.mode) ? body.mode : "generate_video";
        const script = String(body.script || "").trim();
        if (mode !== "deploy_only" && !script) return json({ error: "Script mancante" }, 400);
        if (script.length > 6000) return json({ error: "Script troppo lungo" }, 400);

        const requestId = crypto.randomUUID().replaceAll("-", "").slice(0, 12);
        const inputs = {
          request_id: requestId,
          mode,
          script,
          hook: String(body.hook || "").slice(0, 300),
          voice: String(body.voice || "it-IT-DiegoNeural"),
          rate: String(body.rate || "+6%"),
          outro: String(body.outro || "OROLOGI SPIEGATI SEMPLICE ⌚").slice(0, 300),
          visual_queries: String(body.visual_queries || "").slice(0, 1000)
        };

        await github(env, `/repos/${OWNER}/${REPO}/actions/workflows/${WORKFLOW}/dispatches`, {
          method: "POST",
          body: JSON.stringify({ ref: REF, inputs })
        });

        return json({ ok: true, request_id: requestId });
      }

      if (url.pathname === "/api/status" && request.method === "GET") {
        const id = (url.searchParams.get("id") || "").replace(/[^a-zA-Z0-9]/g, "");
        if (!id) return json({ error: "ID mancante" }, 400);

        const runs = await github(env, `/repos/${OWNER}/${REPO}/actions/workflows/${WORKFLOW}/runs?event=workflow_dispatch&per_page=30`);
        const run = (runs.workflow_runs || []).find(r => r.display_title === `video-${id}`);

        if (!run) return json({ status: "not_found" });
        return json({
          status: run.status,
          conclusion: run.conclusion,
          run_url: run.html_url,
          run_id: run.id
        });
      }

      if (url.pathname === "/api/health") return json({ ok: true });
      return json({ error: "Endpoint non trovato" }, 404);
    } catch (error) {
      return json({ error: error.message || "Errore interno" }, 500);
    }
  }
};
