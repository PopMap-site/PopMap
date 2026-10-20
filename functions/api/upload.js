// POPMAP — réception des vidéos envoyées par le public (Cloudflare Pages Functions).
// Les fichiers sont déposés, par morceaux de 1 Mo, dans le dépôt GitHub PRIVÉ PopMap-site/PopMap-inbox.
// Rien n'est publié ici : la validation se fait ensuite (tools/video_review.py).
import { Buffer } from "node:buffer";

const REPO = "PopMap-site/PopMap-inbox";
const PART = 1024 * 1024;              // 1 Mo par morceau (limite de calcul des fonctions gratuites)
const MAX_SIZE = 80 * 1024 * 1024;     // 80 Mo par vidéo
const MAX_PARTS = Math.ceil(MAX_SIZE / PART);

const json = (o, s = 200) => new Response(JSON.stringify(o), { status: s, headers: { "content-type": "application/json", "cache-control": "no-store" } });
const clip = (s, n) => String(s ?? "").replace(/[\u0000-\u001f]/g, " ").trim().slice(0, n);
const okId = (id) => /^[0-9]{8}-[0-9]{6}-[a-f0-9]{8}$/.test(id || "");

async function sign(env, id) {
  const k = await crypto.subtle.importKey("raw", new TextEncoder().encode(env.GH_TOKEN || "x"), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const s = await crypto.subtle.sign("HMAC", k, new TextEncoder().encode("popmap:" + id));
  return [...new Uint8Array(s)].slice(0, 16).map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function gh(env, path, b64, message, tries = 4) {
  const url = `${env.GH_API || "https://api.github.com"}/repos/${REPO}/contents/${path}`;
  for (let i = 0; i < tries; i++) {
    const r = await fetch(url, {
      method: "PUT",
      headers: { authorization: `Bearer ${env.GH_TOKEN}`, "user-agent": "popmap-upload", accept: "application/vnd.github+json", "content-type": "application/json" },
      body: `{"message":${JSON.stringify(message)},"content":"${b64}"}`,
    });
    if (r.ok || r.status === 422) return true; // 422 : le fichier existe déjà (morceau renvoyé)
    if (r.status === 409 || r.status >= 500) { await new Promise((ok) => setTimeout(ok, 400 * (i + 1) + Math.random() * 400)); continue; }
    console.error("GitHub", r.status, await r.text());
    return false;
  }
  return false;
}

export async function onRequestPost({ request, env }) {
  if (!env.GH_TOKEN) return json({ error: "Envoi pas encore configuré" }, 503);
  const u = new URL(request.url);
  const act = u.searchParams.get("act");

  if (act === "init") {
    let m;
    try { m = await request.json(); } catch { return json({ error: "Requête invalide" }, 400); }
    if (m.website) return json({ error: "Requête invalide" }, 400); // piège à robots
    const size = Number(m.size);
    if (!(size > 0 && size <= MAX_SIZE)) return json({ error: "Vidéo trop lourde (80 Mo maximum)" }, 413);
    if (!/^video\//.test(m.type || "")) return json({ error: "Ce fichier n'est pas une vidéo" }, 415);
    if (m.consent !== true || m.own !== true) return json({ error: "Merci de cocher les conditions" }, 400);
    const lat = Number(m.lat), lon = Number(m.lon);
    const d = new Date();
    const rnd = [...crypto.getRandomValues(new Uint8Array(4))].map((b) => b.toString(16).padStart(2, "0")).join("");
    const id = `${d.toISOString().replace(/[-:]/g, "").replace("T", "-").slice(0, 15)}-${rnd}`;
    const meta = {
      id, status: "incomplete", received: d.toISOString(),
      size, type: clip(m.type, 40), name: clip(m.name, 120),
      place: clip(m.place, 120), lat: m.lat != null && Number.isFinite(lat) ? lat : null, lon: m.lon != null && Number.isFinite(lon) ? lon : null,
      when: clip(m.when, 40), desc: clip(m.desc, 600), email: clip(m.email, 120),
      parts: Math.ceil(size / PART),
      // Données techniques conservées un an pour répondre à une éventuelle réquisition judiciaire (LCEN) — jamais publiées.
      ip: request.headers.get("cf-connecting-ip") || "", ua: clip(request.headers.get("user-agent"), 200),
    };
    const ok = await gh(env, `inbox/${id}/meta.json`, Buffer.from(JSON.stringify(meta, null, 1)).toString("base64"), `Nouvel envoi ${id}`);
    if (!ok) return json({ error: "Envoi impossible pour le moment, réessaie plus tard" }, 502);
    return json({ id, t: await sign(env, id), parts: meta.parts, partSize: PART });
  }

  const id = u.searchParams.get("id"), t = u.searchParams.get("t");
  if (!okId(id) || t !== (await sign(env, id))) return json({ error: "Envoi non reconnu" }, 403);

  if (act === "part") {
    const n = Number(u.searchParams.get("n"));
    if (!(Number.isInteger(n) && n >= 0 && n < MAX_PARTS)) return json({ error: "Morceau invalide" }, 400);
    const buf = Buffer.from(await request.arrayBuffer());
    if (!buf.length || buf.length > PART) return json({ error: "Morceau trop gros" }, 413);
    const ok = await gh(env, `inbox/${id}/part-${String(n).padStart(3, "0")}`, buf.toString("base64"), `${id} morceau ${n}`);
    return ok ? json({ ok: true }) : json({ error: "Échec de l'envoi, réessaie" }, 502);
  }

  if (act === "done") {
    const n = Number(u.searchParams.get("parts"));
    const ok = await gh(env, `inbox/${id}/done.json`, Buffer.from(JSON.stringify({ id, parts: n, at: new Date().toISOString() })).toString("base64"), `${id} complet`);
    return ok ? json({ ok: true }) : json({ error: "Échec de la finalisation, réessaie" }, 502);
  }
  return json({ error: "Action inconnue" }, 400);
}

