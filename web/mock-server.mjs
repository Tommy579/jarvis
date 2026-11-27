import http from "node:http";
import { WebSocketServer } from "ws";

const args = process.argv.slice(2);
const portIdx = args.indexOf("--port");
const PORT = portIdx >= 0 ? Number(args[portIdx + 1]) : 8765;

const db = {
  messages: [
    {
      id: "msg_boot_1",
      role: "assistant",
      content: "Service de simulation pret. Posez une question ou testez les etats de la sphere.",
      created_at: new Date().toISOString(),
      usage: { model: "deepseek/deepseek-v4.1-flash-1017", tokens_in: 18, tokens_out: 14, cost: 0.0001 },
      tools: [],
    },
  ],
  facts: [
    {
      id: "fact_1",
      subject: "Tom",
      predicate: "os_bureau",
      object: "Arch Linux KDE Wayland",
      text: "Poste principal sous Arch Linux et KDE Plasma Wayland.",
      category: "travail",
      confidence: 0.96,
      source: "onboarding",
      sensitivity: "normal",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      last_used_at: null,
      superseded_by: null,
    },
    {
      id: "fact_2",
      subject: "Tom",
      predicate: "preference_cafe",
      object: "Expresso sans sucre",
      text: "Boit un expresso sans sucre le matin.",
      category: "gouts",
      confidence: 0.55,
      source: "conversation",
      sensitivity: "normal",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      last_used_at: null,
      superseded_by: null,
    },
  ],
  wiki: {
    moi: {
      slug: "moi",
      title: "Profil personnel (Tom)",
      content: "# Profil personnel (Tom)\n\n- Developpeur sous Arch Linux / KDE Wayland.\n- Prefere les reponses concises.",
      updated_at: new Date().toISOString(),
    },
    regles: {
      slug: "regles",
      title: "Regles pour Jarvis",
      content: "# Regles pour Jarvis\n\n- Heures calmes entre 23:00 et 08:00.\n- Toujours confirmer les actions niveau 2 et 3.",
      updated_at: new Date().toISOString(),
    },
  },
  people: [
    {
      id: "pers_1",
      name: "Maman",
      relation: "Famille (VIP)",
      birthday: "1968-04-12",
      contact_ref: "maman@exemple.fr",
      notes: "Contact prioritaire.",
    },
  ],
  models: [
    {
      role: "agent",
      model: "deepseek/deepseek-v4.1-flash-1017",
      provider: "openrouter",
      fallback: ["z-ai/glm-5.3", "minimax/minimax-m3", "google/gemini-3.5-flash-lite"],
      thinking_mode: "auto",
      supports_vision: false,
      supports_tools: true,
    },
    {
      role: "triage",
      model: "gemini-3.5-flash-lite",
      provider: "gemini",
      fallback: ["google/gemini-3.5-flash-lite"],
      thinking_mode: "auto",
      supports_vision: true,
      supports_tools: true,
    },
    {
      role: "vision",
      model: "gemini-3.5-flash-lite",
      provider: "gemini",
      fallback: [],
      thinking_mode: "auto",
      supports_vision: true,
      supports_tools: false,
    },
  ],
  usage: [
    { date: "2026-11-13", model: "deepseek/deepseek-v4.1-flash-1017", calls: 42, tokens_in: 18400, tokens_out: 4120, cost: 0.014 },
    { date: "2026-11-14", model: "deepseek/deepseek-v4.1-flash-1017", calls: 19, tokens_in: 8200, tokens_out: 1950, cost: 0.006 },
  ],
  evals: {
    run_at: new Date().toISOString(),
    total: 3,
    passed: 3,
    scenarios: [
      { id: "eval_oral", title: "Registre oral <= 2 phrases", model: "deepseek/deepseek-v4.1-flash-1017", passed: true, latency_ms: 410, score: 1.0, notes: "Conforme" },
      { id: "eval_tool", title: "Appel web_search sans ouverture onglet", model: "deepseek/deepseek-v4.1-flash-1017", passed: true, latency_ms: 530, score: 1.0, notes: "Conforme" },
      { id: "eval_inj", title: "Blocage injection <untrusted>", model: "deepseek/deepseek-v4.1-flash-1017", passed: true, latency_ms: 490, score: 1.0, notes: "Niveau 2 bloque" },
    ],
  },
  alerts: [
    {
      id: "alert_1",
      ts: new Date().toISOString(),
      channel: "web",
      urgency: 2,
      message: "Reunion projet dans 15 minutes.",
      reason: "Regle deterministe calendar.upcoming",
      status: "sent",
      acked_at: null,
    },
  ],
  approvals: [
    {
      id: "appr_demo_1",
      tool_name: "restart_service",
      description: "Redemarrer le service nginx sur le serveur personnel ?",
      args: { service: "nginx" },
      risk: 3,
      created_at: new Date().toISOString(),
      expires_at: new Date(Date.now() + 120000).toISOString(),
      status: "pending",
    },
  ],
  rules: {
    quiet_start: "23:00",
    quiet_end: "08:00",
    timezone: "Europe/Paris",
    max_calls_per_day: 3,
    min_urgency_for_call: 3,
    vip_contacts: [{ name: "Maman", identifiers: ["maman"], default_urgency: 2 }],
  },
  integrations: [
    {
      id: "google_calendar",
      name: "Google Calendar",
      priority: 1,
      enabled: true,
      connected: true,
      last_sync: new Date().toISOString(),
      description: "Agenda et rappels",
      tools: ["calendar_list_events"],
      auth_type: "oauth",
      oauth_provider: "google",
      authorize_url: "https://developers.google.com/oauthplayground/#step1&apisSelect=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.readonly%2Chttps%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.compose%2Chttps%3A%2F%2Fwww.googleapis.com%2Fauth%2Fcalendar.events%2Chttps%3A%2F%2Fwww.googleapis.com%2Fauth%2Fcontacts.readonly",
      oauth_client_configured: false,
    },
    {
      id: "gmail",
      name: "Gmail",
      priority: 1,
      enabled: false,
      connected: false,
      last_sync: null,
      description: "Lecture filtree et brouillons",
      tools: ["mail_search", "mail_draft"],
      auth_type: "oauth",
      oauth_provider: "google",
      authorize_url: "https://developers.google.com/oauthplayground/#step1&apisSelect=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.readonly%2Chttps%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.compose%2Chttps%3A%2F%2Fwww.googleapis.com%2Fauth%2Fcalendar.events%2Chttps%3A%2F%2Fwww.googleapis.com%2Fauth%2Fcontacts.readonly",
      oauth_client_configured: false,
    },
    {
      id: "home_assistant",
      name: "Home Assistant",
      priority: 2,
      enabled: true,
      connected: true,
      last_sync: new Date().toISOString(),
      description: "Presence et domotique",
      tools: ["home_get_states"],
      auth_type: "api_key",
      oauth_provider: null,
      authorize_url: null,
      oauth_client_configured: false,
    },
    {
      id: "weather_transport",
      name: "Meteo et Transports",
      priority: 3,
      enabled: true,
      connected: true,
      last_sync: new Date().toISOString(),
      description: "Open-Meteo et Navitia",
      tools: ["weather_forecast"],
      auth_type: "api_key",
      oauth_provider: null,
      authorize_url: null,
      oauth_client_configured: false,
    },
  ],
  blacklist: {
    apps: ["keepassxc", "bitwarden", "1password", "banking"],
    global_paused: false,
    sensors: [
      { sensor: "screen", active: true, paused: false, detail: "Capture a la demande" },
      { sensor: "mic", active: false, paused: true, detail: "Micro en pause" },
      { sensor: "camera", active: false, paused: true, detail: "Presence desactivee" },
    ],
  },
  onboarding: {
    completed_modules: 1,
    total_modules: 10,
    quarterly_check_due: false,
    daily_question: {
      id: "wake_sleep",
      field_key: "wake_sleep",
      category: "rythme",
      sensitivity: "normal",
      prompt: "A quelles heures te leves-tu et te couches-tu habituellement ?",
      placeholder: "07:30 / 23:30",
      already_known: false,
      known_value: null,
    },
    modules: [
      {
        id: "identite",
        title: "Identite",
        description: "Prenom, surnom souhaite et tutoiement.",
        completed: true,
        answered_count: 1,
        total_count: 1,
        skipped_count: 0,
        questions: [
          {
            id: "preferred_name",
            field_key: "preferred_name",
            category: "identite",
            sensitivity: "normal",
            prompt: "Comment veux-tu que Jarvis t'appelle ?",
            placeholder: "Tom",
            already_known: true,
            known_value: "Tom",
          },
        ],
      },
      {
        id: "rythme",
        title: "Rythme",
        description: "Horaires habituels et plages de concentration.",
        completed: false,
        answered_count: 0,
        total_count: 1,
        skipped_count: 0,
        questions: [
          {
            id: "wake_sleep",
            field_key: "wake_sleep",
            category: "rythme",
            sensitivity: "normal",
            prompt: "A quelles heures te leves-tu et te couches-tu habituellement ?",
            placeholder: "07:30 / 23:30",
            already_known: false,
            known_value: null,
          },
        ],
      },
    ],
  },
};

function readBody(req) {
  return new Promise((resolve) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf8");
      try {
        resolve(raw ? JSON.parse(raw) : {});
      } catch {
        resolve({});
      }
    });
  });
}

function sendJson(res, status, data) {
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
  });
  res.end(JSON.stringify(data));
}

db.backups = {
  directory: "/tmp/jarvis-mock/backups",
  keep: 7,
  last_backup_at: new Date(Date.now() - 26 * 3600 * 1000).toISOString(),
  backups: [
    {
      name: "jarvis-20261126T032000Z.db",
      size_bytes: 245760,
      created_at: new Date(Date.now() - 26 * 3600 * 1000).toISOString(),
    },
  ],
};

db.autostart = {
  enabled: true,
  supported: true,
  desktop_file: "~/.config/autostart/JarvisAssistant.desktop",
  detail: "Active au demarrage de la session.",
};

const clients = new Set();
function broadcast(msg) {
  const raw = JSON.stringify(msg);
  for (const ws of clients) {
    if (ws.readyState === 1) ws.send(raw);
  }
}

const server = http.createServer(async (req, res) => {
  if (req.method === "OPTIONS") {
    res.writeHead(204, {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type,Authorization",
    });
    res.end();
    return;
  }

  const url = new URL(req.url || "/", `http://127.0.0.1:${PORT}`);
  const p = url.pathname;

  if (p === "/api/health") return sendJson(res, 200, { status: "ok", mode: "mock" });
  if (p === "/api/version" && req.method === "GET") {
    return sendJson(res, 200, {
      version: "2.0.1",
      schema_version: 5,
      python: "3.12.13",
      platform: "Linux (mock)",
      data_dir: "/tmp/jarvis-mock",
      started_at: new Date(Date.now() - 3_600_000).toISOString(),
      uptime_seconds: 3600,
    });
  }
  if (p === "/api/updates/check" && req.method === "GET") {
    return sendJson(res, 200, {
      current_version: "2.0.1",
      latest_version: "2.0.1",
      update_available: false,
      release_url: null,
      published_at: null,
      checked_at: new Date().toISOString(),
      detail: "",
    });
  }
  if (p === "/api/backups" && req.method === "GET") return sendJson(res, 200, db.backups);
  if (p === "/api/backups" && req.method === "POST") {
    const now = new Date();
    const stamp = now.toISOString().replace(/[-:]/g, "").slice(0, 15);
    db.backups = {
      ...db.backups,
      last_backup_at: now.toISOString(),
      backups: [
        { name: `jarvis-${stamp}Z.db`, size_bytes: 262144, created_at: now.toISOString() },
        ...db.backups.backups,
      ],
    };
    return sendJson(res, 200, db.backups);
  }
  if (p === "/api/doctor" && req.method === "GET") {
    const checks = [
      { name: "python", status: "ok", detail: "3.12.13" },
      { name: "environnement", status: "ok", detail: "venv de simulation" },
      { name: "fichier .env", status: "ok", detail: "present, permissions 600." },
      { name: "cle LLM", status: "ok", detail: "OPENROUTER_API_KEY" },
      { name: "donnees", status: "ok", detail: "/tmp/jarvis-mock accessible en ecriture." },
      { name: "base memoire", status: "ok", detail: "Intege, schema v5." },
      { name: "sauvegardes", status: "ok", detail: "jarvis-20261126T032000Z.db, il y a 26.0 h." },
      { name: "interface web", status: "ok", detail: "web/dist compile." },
      { name: "port 8765", status: "ok", detail: "service actif sur 127.0.0.1:8765." },
      { name: "espace disque", status: "ok", detail: "42.0 Go libres." },
    ];
    return sendJson(res, 200, { status: "ok", report: "", checks });
  }
  if (p === "/api/system/autostart" && req.method === "GET") {
    return sendJson(res, 200, db.autostart);
  }
  if (p === "/api/system/autostart" && req.method === "PUT") {
    const body = await readBody(req);
    db.autostart = { ...db.autostart, enabled: Boolean(body?.enabled) };
    return sendJson(res, 200, db.autostart);
  }
  if (p === "/api/messages" && req.method === "GET") return sendJson(res, 200, db.messages);
  if (p === "/api/facts" && req.method === "GET") {
    const q = (url.searchParams.get("q") || "").toLowerCase();
    const cat = url.searchParams.get("category") || "";
    const sens = url.searchParams.get("sensitivity") || "";
    const filtered = db.facts.filter(
      (f) =>
        (!q || f.text.toLowerCase().includes(q)) &&
        (!cat || f.category === cat) &&
        (!sens || f.sensitivity === sens)
    );
    return sendJson(res, 200, filtered);
  }
  if (p === "/api/facts/review" && req.method === "GET") {
    return sendJson(res, 200, [...db.facts].sort((a, b) => a.confidence - b.confidence).slice(0, 10));
  }
  if (p === "/api/facts" && req.method === "POST") {
    const body = await readBody(req);
    const created = {
      id: `fact_${Date.now()}`,
      subject: body.subject || "Tom",
      predicate: body.predicate || "note",
      object: body.object || body.text || "",
      text: body.text || "",
      category: body.category || "general",
      confidence: body.confidence ?? 0.95,
      source: body.source || "manual",
      sensitivity: body.sensitivity || "normal",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      last_used_at: null,
      superseded_by: null,
    };
    db.facts.unshift(created);
    return sendJson(res, 200, created);
  }
  if (p.startsWith("/api/facts/") && req.method === "PATCH") {
    const id = p.split("/").pop();
    const body = await readBody(req);
    const idx = db.facts.findIndex((f) => f.id === id);
    if (idx < 0) return sendJson(res, 404, { detail: "Not found" });
    db.facts[idx] = { ...db.facts[idx], ...body, updated_at: new Date().toISOString() };
    return sendJson(res, 200, db.facts[idx]);
  }
  if (p.startsWith("/api/facts/") && req.method === "DELETE") {
    const id = p.split("/").pop();
    db.facts = db.facts.filter((f) => f.id !== id);
    return sendJson(res, 200, { deleted: true });
  }
  if (p === "/api/wiki" && req.method === "GET") return sendJson(res, 200, Object.values(db.wiki));
  if (p.startsWith("/api/wiki/") && req.method === "PUT") {
    const slug = p.split("/").pop();
    const body = await readBody(req);
    db.wiki[slug] = {
      slug,
      title: slug,
      content: body.content || "",
      updated_at: new Date().toISOString(),
    };
    return sendJson(res, 200, db.wiki[slug]);
  }
  if (p === "/api/people" && req.method === "GET") return sendJson(res, 200, db.people);
  if (p === "/api/memory/export" && req.method === "GET") {
    return sendJson(res, 200, {
      exported_at: new Date().toISOString(),
      facts: db.facts,
      people: db.people,
      wiki_pages: Object.values(db.wiki),
      markdown_bundle: db.facts.map((f) => `- ${f.text}`).join("\n"),
    });
  }
  if (p === "/api/onboarding/status" && req.method === "GET") return sendJson(res, 200, db.onboarding);
  if (p.startsWith("/api/onboarding/") && p.endsWith("/answer") && req.method === "POST") {
    const modId = p.split("/")[3];
    const body = await readBody(req);
    db.onboarding.modules = db.onboarding.modules.map((m) =>
      m.id === modId ? { ...m, completed: true, answered_count: m.total_count } : m
    );
    db.onboarding.completed_modules = db.onboarding.modules.filter((m) => m.completed).length;
    for (const [k, v] of Object.entries(body.answers || {})) {
      db.facts.unshift({
        id: `fact_${Date.now()}`,
        subject: "Tom",
        predicate: k,
        object: String(v),
        text: `${modId} (${k}) : ${v}`,
        category: modId,
        confidence: 0.98,
        source: "onboarding",
        sensitivity: "normal",
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        last_used_at: null,
        superseded_by: null,
      });
    }
    return sendJson(res, 200, db.onboarding);
  }
  if (p === "/api/models" && req.method === "GET") return sendJson(res, 200, db.models);
  if (p === "/api/models/role" && req.method === "PUT") {
    const body = await readBody(req);
    db.models = db.models.map((m) =>
      m.role === body.role
        ? {
            ...m,
            model: body.model,
            ...(body.thinking_mode ? { thinking_mode: body.thinking_mode } : {}),
          }
        : m
    );
    return sendJson(res, 200, db.models.find((m) => m.role === body.role) || body);
  }
  if (p === "/api/usage/daily" && req.method === "GET") return sendJson(res, 200, db.usage);
  if (p === "/api/evals/latest" && req.method === "GET") return sendJson(res, 200, db.evals);
  if (p === "/api/alerts" && req.method === "GET") return sendJson(res, 200, db.alerts);
  if (p.startsWith("/api/alerts/") && (p.endsWith("/ack") || p.endsWith("/too-noisy")) && req.method === "POST") {
    const id = p.split("/")[3];
    const tooNoisy = p.endsWith("/too-noisy");
    db.alerts = db.alerts.map((a) =>
      a.id === id ? { ...a, status: tooNoisy ? "too_noisy" : "acked", acked_at: new Date().toISOString() } : a
    );
    return sendJson(res, 200, db.alerts.find((a) => a.id === id) || {});
  }
  if (p === "/api/rules" && req.method === "GET") return sendJson(res, 200, db.rules);
  if (p === "/api/rules" && req.method === "PUT") {
    db.rules = await readBody(req);
    return sendJson(res, 200, db.rules);
  }
  if (p === "/api/approvals" && req.method === "GET") return sendJson(res, 200, db.approvals);
  if (p.startsWith("/api/approvals/") && req.method === "POST") {
    const id = p.split("/").pop();
    const body = await readBody(req);
    db.approvals = db.approvals.map((a) =>
      a.id === id ? { ...a, status: body.approved ? "approved" : "denied" } : a
    );
    return sendJson(res, 200, db.approvals.find((a) => a.id === id) || {});
  }
  if (p === "/api/integrations" && req.method === "GET") return sendJson(res, 200, db.integrations);
  if (p.startsWith("/api/integrations/") && p.endsWith("/toggle") && req.method === "POST") {
    const id = p.split("/")[3];
    db.integrations = db.integrations.map((i) => (i.id === id ? { ...i, enabled: !i.enabled } : i));
    return sendJson(res, 200, db.integrations.find((i) => i.id === id) || {});
  }
  if (p.startsWith("/api/integrations/") && p.endsWith("/oauth/url") && req.method === "GET") {
    const id = p.split("/")[3];
    const found = db.integrations.find((i) => i.id === id);
    if (!found || !found.authorize_url) return sendJson(res, 400, { detail: "Lien OAuth indisponible" });
    return sendJson(res, 200, {
      url: found.authorize_url,
      mode: found.oauth_client_configured ? "redirect" : "external",
    });
  }
  if (p === "/api/sensors/blacklist" && req.method === "GET") return sendJson(res, 200, db.blacklist);
  if (p === "/api/sensors/blacklist" && req.method === "PUT") {
    db.blacklist = await readBody(req);
    return sendJson(res, 200, db.blacklist);
  }
  if (p === "/api/usage/budget" && req.method === "GET") {
    return sendJson(res, 200, { daily_budget_usd: db.daily_budget_usd ?? 5.0 });
  }
  if (p === "/api/usage/budget" && req.method === "PUT") {
    const body = await readBody(req);
    db.daily_budget_usd = Number(body.daily_budget_usd ?? 5.0);
    return sendJson(res, 200, { daily_budget_usd: db.daily_budget_usd });
  }
  if (p === "/api/goals/models" && req.method === "GET") {
    return sendJson(res, 200, [
      {
        id: "gemini-4-argon",
        provider: "gemini",
        label: "gemini-4-argon",
        tier: "cheap",
        strengths: "Orchestration et verification",
        in_cost_per_m: 0.3,
        out_cost_per_m: 1.2,
        supports_vision: true,
        best_for: ["orchestrator", "verifier"],
      },
      {
        id: "deepseek/deepseek-v4.1-flash-1017",
        provider: "openrouter",
        label: "deepseek-v4.1-flash-1017",
        tier: "ultra_cheap",
        strengths: "Code et outils rapides",
        in_cost_per_m: 0.15,
        out_cost_per_m: 0.6,
        supports_vision: false,
        best_for: ["coder"],
      },
    ]);
  }
  if (p === "/api/goals" && req.method === "GET") {
    return sendJson(res, 200, db.goals || []);
  }
  if (p === "/api/goals" && req.method === "POST") {
    const body = await readBody(req);
    const now = new Date().toISOString();
    const g = {
      id: `goal_${Date.now()}`,
      title: String(body.goal || "Objectif").slice(0, 80),
      goal: String(body.goal || ""),
      status: "running",
      success_criteria: [],
      verified_criteria: [],
      orchestrator_model: body.orchestrator_model || "gemini-4-argon",
      allowed_models: ["gemini-4-argon", "deepseek/deepseek-v4.1-flash-1017"],
      tokens_used: 0,
      cost_usd: 0,
      max_cost_usd: Number(body.max_cost_usd || 1.5),
      progress_pct: 15,
      current_phase: "Supervision mock active",
      final_summary: "",
      pause_reason: null,
      notify_on_finish: true,
      subtasks: [],
      events: [],
      created_at: now,
      updated_at: now,
      finished_at: null,
    };
    db.goals = [g, ...(db.goals || [])];
    return sendJson(res, 200, g);
  }
  if (p.startsWith("/api/goals/") && req.method === "GET") {
    const id = p.split("/")[3];
    const found = (db.goals || []).find((g) => g.id === id);
    return found ? sendJson(res, 200, found) : sendJson(res, 404, { detail: "Objectif introuvable" });
  }
  if (p.startsWith("/api/goals/") && req.method === "POST") {
    const parts = p.split("/");
    const id = parts[3];
    const action = parts[4];
    db.goals = (db.goals || []).map((g) => {
      if (g.id !== id) return g;
      if (action === "pause") return { ...g, status: "paused" };
      if (action === "resume" || action === "steer") return { ...g, status: "running", pause_reason: null };
      if (action === "cancel") return { ...g, status: "cancelled" };
      return g;
    });
    const found = (db.goals || []).find((g) => g.id === id);
    return found ? sendJson(res, 200, found) : sendJson(res, 404, { detail: "Objectif introuvable" });
  }
  if (p === "/mock/emit" && req.method === "POST") {
    const body = await readBody(req);
    // Le vrai jarvis-core persiste l'approbation AVANT de la diffuser en WebSocket :
    // le mock doit faire de meme, sinon /api/approvals et le flux WS divergent.
    if (body?.type === "approval.request" && body?.payload?.id) {
      db.approvals = [
        {
          tool_name: "action_protegee",
          args: {},
          risk: 2,
          created_at: new Date().toISOString(),
          status: "pending",
          ...body.payload,
        },
        ...db.approvals.filter((a) => a.id !== body.payload.id),
      ];
    }
    broadcast(body);
    return sendJson(res, 200, { broadcasted: true });
  }

  sendJson(res, 404, { detail: "Route introuvable" });
});

const wss = new WebSocketServer({ server, path: "/ws" });
wss.on("connection", (ws) => {
  clients.add(ws);
  ws.send(JSON.stringify({ type: "state", payload: { value: "idle" } }));

  ws.on("message", (raw) => {
    try {
      const event = JSON.parse(raw.toString("utf8"));
      if (event.type === "user.text") {
        const text = event.payload?.text || "";
        const msgId = `msg_${Date.now()}`;
        const toolId = `tool_${Date.now()}`;
        ws.send(JSON.stringify({ type: "transcript.final", payload: { text, speaker: "user" } }));
        ws.send(JSON.stringify({ type: "state", payload: { value: "thinking" } }));
        ws.send(
          JSON.stringify({
            type: "tool.call",
            payload: { id: toolId, name: "memory_search", args: { query: text }, risk: 0 },
          })
        );
        setTimeout(() => {
          ws.send(
            JSON.stringify({
              type: "tool.result",
              payload: { id: toolId, ok: true, summary: "2 faits trouves dans la memoire locale." },
            })
          );
          ws.send(JSON.stringify({ type: "state", payload: { value: "speaking" } }));
          const reply = `Bien recu : ${text}. Tout est operationnel sur jarvis-core.`;
          ws.send(JSON.stringify({ type: "assistant.delta", payload: { text: reply, message_id: msgId } }));
          const pcmSample = Buffer.alloc(1024, 12).toString("base64");
          ws.send(JSON.stringify({ type: "tts.chunk", payload: { audio: pcmSample, seq: 1, is_last: true } }));
          ws.send(
            JSON.stringify({
              type: "assistant.done",
              payload: {
                message_id: msgId,
                usage: { model: db.models[0].model, tokens_in: 34, tokens_out: 18, cost: 0.0002 },
              },
            })
          );
          if (text.toLowerCase().includes("approuve") || text.toLowerCase().includes("redemarre")) {
            ws.send(
              JSON.stringify({
                type: "approval.request",
                payload: {
                  id: `appr_${Date.now()}`,
                  description: "Confirmer le redemarrage du service cible (niveau 3) ?",
                  risk: 3,
                  expires_at: new Date(Date.now() + 120000).toISOString(),
                },
              })
            );
          }
          setTimeout(() => {
            ws.send(JSON.stringify({ type: "state", payload: { value: "idle" } }));
          }, 120);
        }, 120);
      } else if (event.type === "action.approve") {
        db.approvals = db.approvals.map((a) =>
          a.id === event.payload.id ? { ...a, status: event.payload.approved ? "approved" : "denied" } : a
        );
      } else if (event.type === "alert.ack") {
        db.alerts = db.alerts.map((a) =>
          a.id === event.payload.id ? { ...a, status: "acked", acked_at: new Date().toISOString() } : a
        );
        ws.send(JSON.stringify({ type: "state", payload: { value: "idle" } }));
      } else if (event.type === "settings.model") {
        db.models = db.models.map((m) =>
          m.role === event.payload.role ? { ...m, model: event.payload.model } : m
        );
      } else if (event.type === "sensors.toggle") {
        db.blacklist.sensors = db.blacklist.sensors.map((s) =>
          s.sensor === event.payload.sensor
            ? { ...s, paused: event.payload.paused, active: !event.payload.paused }
            : s
        );
      }
    } catch {
      // ignore invalid message
    }
  });

  ws.on("close", () => clients.delete(ws));
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`JARVIS mock server ecoute sur http://127.0.0.1:${PORT}`);
});
