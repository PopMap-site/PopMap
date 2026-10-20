// POPMAP — point d'entrée Cloudflare : sert le site (fichiers statiques) et la route d'envoi des vidéos.
import { handleUpload } from "./upload.js";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/api/upload") {
      if (request.method !== "POST") return new Response("Méthode non autorisée", { status: 405 });
      return handleUpload(request, env);
    }
    return env.ASSETS.fetch(request);
  },
};
