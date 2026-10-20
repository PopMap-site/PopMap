#!/usr/bin/env python3
"""POPMAP — examen et publication des vidéos envoyées par le public.

La boîte de réception est le dépôt PRIVÉ PopMap-site/PopMap-inbox (dossier inbox/<id>/ :
meta.json, part-000…, done.json). Ce script s'utilise depuis la racine du dépôt public PopMap.

  python3 tools/video_review.py list    INBOX
  python3 tools/video_review.py prepare INBOX ID          -> /tmp/popmap-review/ID/ (images à examiner)
  python3 tools/video_review.py publish INBOX ID --ev EVENT_ID --cap "Légende" [--place ...] [--lat ..] [--lon ..] [--t AAAA-MM-JJTHH:MM] [--mute]
  python3 tools/video_review.py refuse  INBOX ID --reason "…"
  python3 tools/video_review.py purge   INBOX            (envois incomplets > 2 jours, journaux > 1 an)

Après publish/refuse/purge : committer le dépôt public (videos/, data/videos.json) ET le dépôt inbox.
Dépendances : pip install opencv-python-headless numpy ; ffmpeg.
"""
import argparse, datetime as dt, glob, json, os, shutil, subprocess, sys

import numpy as np

WORK = "/tmp/popmap-review"
MODEL_DIR = os.environ.get("POPMAP_FACE_DIR", os.path.expanduser("~/.cache/popmap-face"))
YUNET = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"


def sh(*a, **k):
    return subprocess.run(list(a), check=True, capture_output=True, text=True, **k).stdout


def meta_of(inbox, i):
    return json.load(open(os.path.join(inbox, "inbox", i, "meta.json")))


def public_meta(m):
    return {k: m.get(k) for k in ("id", "received", "place", "lat", "lon", "when", "desc", "size", "name")}


def pending(inbox):
    out = []
    for d in sorted(glob.glob(os.path.join(inbox, "inbox", "*"))):
        i = os.path.basename(d)
        if os.path.exists(os.path.join(d, "done.json")) and os.path.exists(os.path.join(d, "meta.json")):
            out.append(i)
    return out


def cmd_list(a):
    p = pending(a.inbox)
    print(f"{len(p)} vidéo(s) à examiner")
    for i in p:
        print(json.dumps(public_meta(meta_of(a.inbox, i)), ensure_ascii=False))


# ---------- visages ----------
def detector():
    import cv2
    os.makedirs(MODEL_DIR, exist_ok=True)
    f = os.path.join(MODEL_DIR, "yunet.onnx")
    if not os.path.exists(f) or os.path.getsize(f) < 100000:
        subprocess.run(["curl", "-sSL", "-o", f, YUNET], check=True)
    return cv2.FaceDetectorYN.create(f, "", (320, 320), 0.55, 0.3, 5000)


def blur_video(src, dst, mute=False, max_h=720):
    """Floute chaque visage détecté (avec une marge et une persistance de quelques images)."""
    import cv2
    cap = cv2.VideoCapture(src)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    w0, h0 = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    # 720 px maximum sur le plus petit côté
    s = min(1.0, 720 / min(w0, h0)) if min(w0, h0) > 720 else 1.0
    W, H = int(w0 * s) // 2 * 2, int(h0 * s) // 2 * 2
    raw = dst + ".raw.mp4"
    out = cv2.VideoWriter(raw, cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    det = detector()
    det.setInputSize((W, H))
    recent, n, nfaces = [], 0, 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if (fr.shape[1], fr.shape[0]) != (W, H):
            fr = cv2.resize(fr, (W, H), interpolation=cv2.INTER_AREA)
        boxes = []
        if n % 2 == 0:  # détection une image sur deux (les zones floutées persistent entre deux détections)
            _, faces = det.detect(fr)
            boxes = [] if faces is None else [tuple(map(int, f[:4])) for f in faces]
        if n % 4 == 0:  # et sur une version agrandie pour les petits visages lointains
            big = cv2.resize(fr, (W * 2, H * 2))
            det.setInputSize((W * 2, H * 2))
            _, f2 = det.detect(big)
            det.setInputSize((W, H))
            if f2 is not None:
                boxes += [tuple(int(v / 2) for v in f[:4]) for f in f2]
        nfaces += len(boxes)
        recent.append(boxes)
        recent = recent[-10:]  # persistance ~1/3 s pour éviter les visages qui « clignotent »
        for bx in [b for r in recent for b in r]:
            x, y, bw, bh = bx
            m = int(max(bw, bh) * 0.45)
            x1, y1, x2, y2 = max(0, x - m), max(0, y - m), min(W, x + bw + m), min(H, y + bh + m)
            if x2 - x1 < 4 or y2 - y1 < 4:
                continue
            roi = fr[y1:y2, x1:x2]
            small = cv2.resize(roi, (max(1, (x2 - x1) // 14), max(1, (y2 - y1) // 14)), interpolation=cv2.INTER_LINEAR)
            fr[y1:y2, x1:x2] = cv2.GaussianBlur(cv2.resize(small, (x2 - x1, y2 - y1), interpolation=cv2.INTER_NEAREST), (0, 0), 6)
        out.write(fr)
        n += 1
    cap.release()
    out.release()
    audio = ["-an"] if mute else ["-map", "1:a:0?", "-c:a", "aac", "-b:a", "64k", "-ac", "1"]
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw, "-i", src, "-map", "0:v:0", *audio,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    "-map_metadata", "-1", dst], check=True)
    os.remove(raw)
    return n, nfaces


def frames(src, outdir, k=8):
    os.makedirs(outdir, exist_ok=True)
    d = float(sh("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", src) or 0)
    for j in range(k):
        t = d * (j + 0.5) / k
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", src, "-frames:v", "1",
                        "-vf", "scale=640:-2", os.path.join(outdir, f"f{j + 1:02d}.jpg")], check=True)
    return d


def cmd_prepare(a):
    d = os.path.join(a.inbox, "inbox", a.id)
    m = meta_of(a.inbox, a.id)
    w = os.path.join(WORK, a.id)
    shutil.rmtree(w, ignore_errors=True)
    os.makedirs(w)
    orig = os.path.join(w, "original")
    with open(orig, "wb") as o:
        for p in sorted(glob.glob(os.path.join(d, "part-*"))):
            o.write(open(p, "rb").read())
    if os.path.getsize(orig) != m["size"]:
        print(f"INCOMPLET : {os.path.getsize(orig)} octets reçus sur {m['size']} → refuser (envoi interrompu)")
        return
    try:
        dur = frames(orig, os.path.join(w, "avant"))
    except subprocess.CalledProcessError:
        print("ILLISIBLE : le fichier n'est pas une vidéo lisible → refuser")
        return
    if dur > 180:
        print(f"TROP LONGUE : {dur:.0f} s (3 min max) → refuser ou demander un extrait")
    n, nf = blur_video(orig, os.path.join(w, "floutee.mp4"), mute=a.mute)
    frames(os.path.join(w, "floutee.mp4"), os.path.join(w, "apres"))
    sz = os.path.getsize(os.path.join(w, "floutee.mp4")) / 1e6
    print(json.dumps({**public_meta(m), "duree_s": round(dur, 1), "images": n, "visages_detectes": nf, "taille_publiee_Mo": round(sz, 1),
                      "a_examiner": {"avant": sorted(glob.glob(os.path.join(w, "avant", "*.jpg"))),
                                     "apres_floutage": sorted(glob.glob(os.path.join(w, "apres", "*.jpg")))}}, ensure_ascii=False, indent=1))


def log_decision(inbox, i, decision, extra):
    d = os.path.join(inbox, "inbox", i)
    m = meta_of(inbox, i)
    os.makedirs(os.path.join(inbox, "log"), exist_ok=True)
    m.update({"status": decision, "decided": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z", **extra})
    json.dump(m, open(os.path.join(inbox, "log", f"{i}.json"), "w"), ensure_ascii=False, indent=1)
    shutil.rmtree(d)  # la vidéo d'origine n'est plus conservée


def cmd_publish(a):
    w = os.path.join(WORK, a.id)
    v = os.path.join(w, "floutee.mp4")
    if not os.path.exists(v):
        sys.exit("Lance d'abord : prepare")
    if a.mute:  # republier sans le son
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", v, "-c:v", "copy", "-an", v + ".m.mp4"], check=True)
        os.replace(v + ".m.mp4", v)
    m = meta_of(a.inbox, a.id)
    E = {e["id"]: e for e in json.load(open("data/events.json"))}
    if a.ev not in E:
        sys.exit(f"Fiche inconnue : {a.ev}")
    os.makedirs("videos", exist_ok=True)
    shutil.copy(v, f"videos/{a.id}.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "0.5", "-i", v, "-frames:v", "1", "-vf", "scale=720:-2", "-q:v", "5", f"videos/{a.id}.jpg"], check=True)
    lat = a.lat if a.lat is not None else (m.get("lat") if m.get("lat") is not None else E[a.ev]["lat"])
    lon = a.lon if a.lon is not None else (m.get("lon") if m.get("lon") is not None else E[a.ev]["lon"])
    vids = json.load(open("data/videos.json")) if os.path.exists("data/videos.json") else []
    vids = [x for x in vids if x["id"] != a.id]
    vids.append({"id": a.id, "ev": a.ev, "place": a.place or m.get("place") or E[a.ev]["place"],
                 "lat": round(float(lat), 3), "lon": round(float(lon), 3),  # position arrondie (~100 m)
                 "t": a.t or (m.get("when") or "")[:16], "ta": True, "cap": a.cap,
                 "f": f"videos/{a.id}.mp4", "poster": f"videos/{a.id}.jpg", "added": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"})
    json.dump(vids, open("data/videos.json", "w"), ensure_ascii=False, indent=1)
    log_decision(a.inbox, a.id, "publiee", {"ev": a.ev})
    print(f"Publiée : videos/{a.id}.mp4 sur la fiche {a.ev}")


def cmd_refuse(a):
    log_decision(a.inbox, a.id, "refusee", {"reason": a.reason})
    shutil.rmtree(os.path.join(WORK, a.id), ignore_errors=True)
    print(f"Refusée et supprimée : {a.id}")


def cmd_purge(a):
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    n = 0
    for d in glob.glob(os.path.join(a.inbox, "inbox", "*")):
        if not os.path.exists(os.path.join(d, "done.json")):
            try:
                r = dt.datetime.fromisoformat(meta_of(a.inbox, os.path.basename(d))["received"].replace("Z", ""))
            except Exception:
                r = now - dt.timedelta(days=3)
            if now - r > dt.timedelta(days=2):
                shutil.rmtree(d); n += 1
    for f in glob.glob(os.path.join(a.inbox, "log", "*.json")):
        try:
            r = dt.datetime.fromisoformat(json.load(open(f))["received"].replace("Z", ""))
            if now - r > dt.timedelta(days=365):
                os.remove(f); n += 1
        except Exception:
            pass
    print(f"{n} élément(s) supprimé(s)")


if __name__ == "__main__":
    P = argparse.ArgumentParser()
    S = P.add_subparsers(dest="c", required=True)
    for c in ("list", "prepare", "publish", "refuse", "purge"):
        q = S.add_parser(c)
        q.add_argument("inbox")
        if c in ("prepare", "publish", "refuse"):
            q.add_argument("id")
        if c in ("prepare", "publish"):
            q.add_argument("--mute", action="store_true")
        if c == "publish":
            q.add_argument("--ev", required=True); q.add_argument("--cap", required=True)
            q.add_argument("--place"); q.add_argument("--lat", type=float); q.add_argument("--lon", type=float); q.add_argument("--t")
        if c == "refuse":
            q.add_argument("--reason", required=True)
    a = P.parse_args()
    globals()["cmd_" + a.c](a)
