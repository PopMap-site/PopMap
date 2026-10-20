// POPMAP — réception des vidéos envoyées par le public.
// Les fichiers sont déposés, par morceaux, dans le dépôt GitHub PRIVÉ PopMap-site/PopMap-inbox.
// Rien n'est publié ici : la validation se fait ensuite (tools/video_review.py).
import crypto from "node:crypto";

const REPO = "PopMap-site/PopMap-inbox";
const MAX_SIZE = 80 * 1024 * 1024;     // 80 Mo par vidéo
const MAX_PART = 3 * 1024 * 1024 + 16; // morceaux de 3 Mo
const MAX_PARTS = Math.ceil(MAX_SIZE / (3 * 1024 * 1024));

const json = (o, s = 200) => new Response(JSON.stringify(o), { status: s, headers: { "content-type": "application/json", "cache-control": "no-store" } });
const sign = (id) => crypto.createHmac("sha256", process.env.GH_TOKEN || "x").update("popmap:" + id).digest("hex").slice(0, 32);
const okId = (id) => /^[0-9]{8}-[0-9]{6}-[a-f0-9]{8}$/.test(id || "");

async function gh(path, body, tries = 4) {
  const url = `https://api.github.com/repos/${REPO}/contents/${path}`;
  for (let i = 0; i < tries; i++) {
    const r = await fetch(url, {
      method: "PUT",
      headers: { authorization: `Bearer ${process.env.GH_TOKEN}`, "user-agent": "popmap-upload", accept: "application/vnd.github+json" },
      body: JSON.stringify(body),
    });
    if (r.ok) return true;
    if (r.status === 422) return true; // le fichier existe déjà (morceau renvoyé) : on considère que c'est bon
    if (r.status === 409 || r.status >= 500) { await new Promise((ok) => setTimeout(ok, 400 * (i + 1) + Math.random() * 400)); continue; }
    console.error("GitHub", r.status, await r.text());
    return false;
  }
  return false;
}
const clip = (s, n) => String(s ?? "").replace(/[\u0000-\u001f]/g, " ").trim().slice(0, n);

export default async (req) => {
  if (req.method !== "POST") return json({ error: "Méthode non autorisée" }, 405);
  if (!process.env.GH_TOKEN) return json({ error: "Envoi pas encore configuré" }, 503);
  const u = new URL(req.url);
  const act = u.searchParams.get("act");

  if (act === "init") {
    let m;
    try { m = await req.json(); } catch { return json({ error: "Requête invalide" }, 400); }
    if (m.website) return json({ error: "Requête invalide" }, 400); // piège à robots
    const size = Number(m.size);
    if (!(size > 0 && size <= MAX_SIZE)) return json({ error: "Vidéo trop lourde (80 Mo maximum)" }, 413);
    if (!/^video\//.test(m.type || "")) return json({ error: "Ce fichier n'est pas une vidéo" }, 415);
    if (m.consent !== true || m.own !== true) return json({ error: "Merci de cocher les conditions" }, 400);
    const lat = Number(m.lat), lon = Number(m.lon);
    const d = new Date();
    const stamp = d.toISOString().replace(/[-:]/g, "").replace("T", "-").slice(0, 15);
    const id = `${stamp}-${crypto.randomBytes(4).toString("hex")}`;
    const meta = {
      id, status: "incomplete", received: d.toISOString(),
      size, type: clip(m.type, 40), name: clip(m.name, 120),
      place: clip(m.place, 120), lat: Number.isFinite(lat) ? lat : null, lon: Number.isFinite(lon) ? lon : null,
      when: clip(m.when, 40), desc: clip(m.desc, 600), email: clip(m.email, 120),
      parts: Math.ceil(size / (3 * 1024 * 1024)),
      // Données techniques conservées pour répondre à une éventuelle réquisition judiciaire (LCEN) — jamais publiées.
      ip: req.headers.get("x-nf-client-connection-ip") || req.headers.get("x-forwarded-for") || "", ua: clip(req.headers.get("user-agent"), 200),
    };
    const ok = await gh(`inbox/${id}/meta.json`, { message: `Nouvel envoi ${id}`, content: Buffer.from(JSON.stringify(meta, null, 1)).toString("base64") });
    if (!ok) return json({ error: "Envoi impossible pour le moment, réessaie plus tard" }, 502);
    return json({ id, t: sign(id), parts: meta.parts, partSize: 3 * 1024 * 1024 });
  }

  const id = u.searchParams.get("id"), t = u.searchParams.get("t");
  if (!okId(id) || t !== sign(id)) return json({ error: "Envoi non reconnu" }, 403);

  if (act === "part") {
    const n = Number(u.searchParams.get("n"));
    if (!(Number.isInteger(n) && n >= 0 && n < MAX_PARTS)) return json({ error: "Morceau invalide" }, 400);
    const buf = Buffer.from(await req.arrayBuffer());
    if (!buf.length || buf.length > MAX_PART) return json({ error: "Morceau trop gros" }, 413);
    const ok = await gh(`inbox/${id}/part-${String(n).padStart(3, "0")}`, { message: `${id} morceau ${n}`, content: buf.toString("base64") });
    return ok ? json({ ok: true }) : json({ error: "Échec de l'envoi, réessaie" }, 502);
  }

  if (act === "done") {
    const n = Number(u.searchParams.get("parts"));
    const ok = await gh(`inbox/${id}/done.json`, { message: `${id} complet`, content: Buffer.from(JSON.stringify({ id, parts: n, at: new Date().toISOString() })).toString("base64") });
    return ok ? json({ ok: true }) : json({ error: "Échec de la finalisation, réessaie" }, 502);
  }
  return json({ error: "Action inconnue" }, 400);
};

export const config = { path: "/api/upload" };
