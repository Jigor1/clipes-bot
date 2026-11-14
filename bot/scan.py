"""Varredura de clipes da Twitch por categoria.

Roda no GitHub Actions a cada 12 h. Passos:
1. Token de app da Twitch (client credentials).
2. Clipes mais vistos da categoria na janela (todas as linguas).
3. Lives ao vivo da categoria marcadas em portugues -> streamers BR conhecidos;
   clipes de cada streamer BR conhecido (pega quem nao chega no topo geral).
4. Idioma: cache por streamer; se desconhecido, ouve o clipe (faster-whisper).
5. Remove ja usados, bloqueados e repeticoes do mesmo lance.
6. Pontua e guarda no banco (state/pool_<lang>.json); relatorio em reports/.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
REPORTS = ROOT / "reports"
CFG = json.loads((ROOT / "config.json").read_text())
API = "https://api.twitch.tv/helix"
NOW = datetime.now(timezone.utc)

PT_STRONG = re.compile(
    r"\b(ao vivo|jogando|hoje|cidade alta|complexo|vem pro|não|nao|você|voce|galera|zerando|"
    r"primeira vez|brasil|pt-?br|mano|pra|tá|ta on|né|muito|agora|sem|então|até|minha|meu|kkk+|deu ruim|"
    r"policial|pol[ií]cia|fuga|assalto|cidade|favela|morro|bora|vamo|fazendo|dia \d+|vida)\b"
    r"|ção|ções|ão\b|ã|õ|ç",
    re.I,
)
PT_WEAK = re.compile(r"\b(de|do|da|que|com|o|os|um|uma|no|na|é|e o|e a)\b", re.I)
EN_WORDS = re.compile(r"\b(the|and|with|you|is|are|this|what|my|of|to|in|on)\b", re.I)


def pt_like(text):
    """Texto parece portugues? (titulo da live / do clipe)"""
    t = text or ""
    strong = len(PT_STRONG.findall(t))
    weak = len(PT_WEAK.findall(t))
    en = len(EN_WORDS.findall(t))
    return strong >= 1 and (strong * 2 + weak) > en or (weak >= 2 and en == 0)


def load(name, default):
    p = STATE / name
    if p.exists():
        return json.loads(p.read_text())
    return default


def save(name, data):
    STATE.mkdir(exist_ok=True)
    (STATE / name).write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True))


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(ts):
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


class Twitch:
    def __init__(self):
        cid = os.environ["TWITCH_CLIENT_ID"].strip()
        sec = os.environ["TWITCH_CLIENT_SECRET"].strip()
        r = requests.post(
            "https://id.twitch.tv/oauth2/token",
            data={"client_id": cid, "client_secret": sec, "grant_type": "client_credentials"},
            timeout=30,
        )
        if r.status_code != 200:
            sys.exit(f"Twitch recusou o login do app ({r.status_code}): {r.text[:200]} | "
                     f"tamanho do ID: {len(cid)}, tamanho do segredo: {len(sec)}")
        self.h = {"Client-Id": cid, "Authorization": "Bearer " + r.json()["access_token"]}
        self.calls = 0

    def get(self, path, params):
        for attempt in range(4):
            r = requests.get(API + path, headers=self.h, params=params, timeout=30)
            self.calls += 1
            if r.status_code == 429:
                reset = int(r.headers.get("Ratelimit-Reset", time.time() + 5))
                time.sleep(max(1, reset - time.time()))
                continue
            if r.status_code >= 500:
                time.sleep(2 + attempt * 3)
                continue
            r.raise_for_status()
            return r.json()
        r.raise_for_status()

    def paged(self, path, params, pages):
        out, cursor = [], None
        for _ in range(pages):
            p = dict(params)
            if cursor:
                p["after"] = cursor
            j = self.get(path, p)
            out += j.get("data", [])
            cursor = j.get("pagination", {}).get("cursor")
            if not cursor:
                break
        return out


def overlaps(a, b):
    """Mesmo lance: mesmo streamer e trechos da live que se cruzam."""
    if a["broadcaster_id"] != b["broadcaster_id"]:
        return False
    if a.get("video_id") and a.get("video_id") == b.get("video_id") and \
            a.get("vod_offset") is not None and b.get("vod_offset") is not None:
        a0, a1 = a["vod_offset"], a["vod_offset"] + a["duration"]
        b0, b1 = b["vod_offset"], b["vod_offset"] + b["duration"]
        return min(a1, b1) - max(a0, b0) > 3
    # sem offset: criados com menos de 90 s de diferenca
    return abs((parse(a["created_at"]) - parse(b["created_at"])).total_seconds()) < 90


_model = None


def detect_lang(clip):
    """Baixa o clipe em baixa qualidade e ouve ate 30 s. Retorna (lang, prob, texto)."""
    global _model
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "c.mp4"
        wav = Path(td) / "c.wav"
        r = subprocess.run(
            ["yt-dlp", "-q", "--no-warnings", "-f", "worst[ext=mp4]/worst", "-o", str(mp4), clip["url"]],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0 or not mp4.exists():
            return None, 0.0, "download falhou: " + (r.stderr or "")[-200:]
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-i", str(mp4), "-t", "30", "-ac", "1", "-ar", "16000", str(wav)],
            check=False, timeout=120,
        )
        if not wav.exists():
            return None, 0.0, "sem audio"
        if _model is None:
            from faster_whisper import WhisperModel
            _model = WhisperModel(CFG.get("whisper_model", "base"), device="cpu", compute_type="int8")
        segs, info = _model.transcribe(str(wav), beam_size=1, vad_filter=True)
        text = " ".join(s.text.strip() for s in segs)[:300]
        return info.language, float(info.language_probability), text


def main():
    tw = Twitch()
    log = []
    games = tw.get("/games", {"name": CFG["game_name"]})["data"]
    if not games:
        sys.exit("Categoria nao encontrada: " + CFG["game_name"])
    game = games[0]
    gid = game["id"]
    start = NOW - timedelta(hours=CFG["window_hours"])

    used = load("used.json", {"clips": {}})
    blocked = set(x.lower() for x in load("blocklist.json", {"streamers": []})["streamers"])
    langcache = load("streamer_lang.json", {})
    pools = {lg: load(f"pool_{lg}.json", {"clips": []}) for lg in CFG["languages"]}

    # 1) topo da categoria
    clips = tw.paged("/clips", {"game_id": gid, "started_at": iso(start), "ended_at": iso(NOW), "first": 100},
                     CFG["category_pages"])
    log.append(f"categoria: {len(clips)} clipes")

    # 2) lives BR ao vivo -> streamers conhecidos
    streams = tw.paged("/streams", {"game_id": gid, "language": "pt", "first": 100}, CFG["pt_stream_pages"])
    for s in streams:
        c = langcache.setdefault(s["user_id"], {"name": s["user_login"]})
        c.update({"lang": "pt", "src": c.get("src", "tag"), "last_seen": iso(NOW), "viewers": s["viewer_count"]})
    # lives sem tag pt mas com titulo em portugues
    others = tw.paged("/streams", {"game_id": gid, "first": 100}, 3)
    hinted = 0
    for s in others:
        if s["user_id"] in langcache or s["language"] == "pt":
            continue
        if pt_like(s.get("title", "")):
            langcache[s["user_id"]] = {"name": s["user_login"], "lang": "?pt", "src": "titulo",
                                       "last_seen": iso(NOW), "viewers": s["viewer_count"]}
            hinted += 1
    log.append(f"lives pt ao vivo: {len(streams)}; suspeitos pelo titulo: {hinted}")

    pt_ids = sorted(
        [k for k, v in langcache.items() if v.get("lang") in ("pt", "?pt")],
        key=lambda k: -langcache[k].get("viewers", 0),
    )[: CFG["max_pt_broadcasters"]]
    seen = {c["id"] for c in clips}
    extra = 0
    for bid in pt_ids:
        j = tw.get("/clips", {"broadcaster_id": bid, "started_at": iso(start), "ended_at": iso(NOW), "first": CFG.get("per_broadcaster_clips", 20)})
        for c in j.get("data", []):
            if c["game_id"] == gid and c["id"] not in seen:
                clips.append(c)
                seen.add(c["id"])
                extra += 1
    log.append(f"clipes extras de streamers BR: {extra}")

    # 3) filtros basicos
    def ok(c):
        return (c["id"] not in used["clips"] and c["broadcaster_name"].lower() not in blocked
                and c["duration"] >= CFG["min_duration"])

    clips = [c for c in clips if ok(c)]

    # 4) idioma
    for c in clips:
        hours = max(1.0, (NOW - parse(c["created_at"])).total_seconds() / 3600)
        c["score"] = round(c["view_count"] * (1 + 6 / (hours + 2)) * (1.15 if c.get("is_featured") else 1), 1)
    clips.sort(key=lambda c: -c["score"])

    checks = 0
    for c in clips:
        cache = langcache.get(c["broadcaster_id"], {})
        lang = cache.get("lang")
        if lang in ("pt", "en", "outro") and cache.get("src") in ("audio", "tag+audio"):
            c["lang"], c["lang_src"] = lang, "streamer(audio)"
            continue
        tag = c.get("language", "")
        # so ouve o audio quando a live NAO esta marcada em pt mas ha sinal de portugues
        # (titulo em portugues ou streamer suspeito); assim nao gasta tempo com o resto
        need_audio = tag != "pt" and lang != "pt" and (lang == "?pt" or pt_like(c.get("title", "")))
        if need_audio and checks < CFG["audio_checks_per_scan"] and c["view_count"] >= min(CFG["min_views"].values()):
            checks += 1
            al, prob, text = detect_lang(c)
            c["transcript"] = text
            if al and prob >= 0.6:
                lg = al if al in ("pt", "en") else "outro"
                c["lang"], c["lang_src"] = lg, f"audio {prob:.2f}"
                langcache[c["broadcaster_id"]] = {**cache, "name": c["broadcaster_name"], "lang": lg,
                                                  "src": "audio", "last_seen": iso(NOW)}
                continue
        if lang in ("pt", "en"):
            c["lang"], c["lang_src"] = lang, "streamer(tag)"
        elif tag in ("pt", "en"):
            c["lang"], c["lang_src"] = tag, "tag"
        else:
            c["lang"], c["lang_src"] = "outro", "tag:" + tag
    log.append(f"idioma checado pelo audio: {checks} clipes")

    # 5) pools por idioma, sem repetir lance, limite por streamer
    stats = {}
    for lg in CFG["languages"]:
        pool = pools[lg]["clips"]
        cutoff = NOW - timedelta(days=CFG["pool_days"])
        pool[:] = [p for p in pool if parse(p["created_at"]) > cutoff and p["id"] not in used["clips"]
                   and p["broadcaster_name"].lower() not in blocked]
        added = 0
        per = {}
        for c in clips:
            if c["lang"] != lg or c["view_count"] < CFG["min_views"][lg]:
                continue
            if per.get(c["broadcaster_id"], 0) >= CFG["max_per_broadcaster_per_scan"]:
                continue
            dup = next((p for p in pool if p["id"] == c["id"] or overlaps(p, c)), None)
            if dup:
                if dup["id"] == c["id"]:
                    dup.update(view_count=c["view_count"], score=c["score"])
                elif c["view_count"] > dup["view_count"]:
                    pool.remove(dup)
                    pool.append(slim(c))
                continue
            if any(overlaps(u, c) for u in used["clips"].values()):
                continue
            pool.append(slim(c))
            per[c["broadcaster_id"]] = per.get(c["broadcaster_id"], 0) + 1
            added += 1
        pool.sort(key=lambda p: -p["score"])
        stats[lg] = {"novos": added, "no_banco": len(pool),
                     "minutos_no_banco": round(sum(p["duration"] for p in pool) / 60, 1)}
        save(f"pool_{lg}.json", pools[lg])

    save("streamer_lang.json", langcache)
    if not (STATE / "used.json").exists():
        save("used.json", used)
    if not (STATE / "blocklist.json").exists():
        save("blocklist.json", {"streamers": []})

    REPORTS.mkdir(exist_ok=True)
    rep = {"quando": iso(NOW), "categoria": game["name"], "game_id": gid, "janela_h": CFG["window_hours"],
           "log": log, "stats": stats, "api_calls": tw.calls,
           "top": {lg: pools[lg]["clips"][:40] for lg in CFG["languages"]}}
    (REPORTS / "latest.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    print(json.dumps({k: rep[k] for k in ("quando", "categoria", "log", "stats", "api_calls")}, ensure_ascii=False, indent=1))


def slim(c):
    keys = ("id", "url", "broadcaster_id", "broadcaster_name", "creator_name", "video_id", "vod_offset",
            "title", "view_count", "created_at", "thumbnail_url", "duration", "language", "lang", "lang_src",
            "score", "transcript", "is_featured")
    d = {k: c.get(k) for k in keys if c.get(k) is not None}
    d["found_at"] = iso(NOW)
    return d


if __name__ == "__main__":
    main()
