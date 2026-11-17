"""Kick: clipes por categoria (teste).

A API oficial da Kick nao tem clipes, entao usamos os enderecos internos do site
(os mesmos que o kick.com usa). Eles ficam atras do Cloudflare; o curl_cffi imita
o Chrome para passar. Tudo que tentamos fica registrado em reports/kick_probe.json
para sabermos o que funcionou.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from curl_cffi import requests as creq

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://kick.com"
HDR = {"Accept": "application/json", "Referer": "https://kick.com/", "Accept-Language": "pt-BR,pt;q=0.9"}


class Probe:
    def __init__(self):
        self.s = creq.Session(impersonate="chrome")
        self.rows = []

    def get(self, url, tag):
        row = {"tag": tag, "url": url}
        try:
            r = self.s.get(url, headers=HDR, timeout=30)
            row["status"] = r.status_code
            row["type"] = r.headers.get("content-type", "")
            body = r.text
            row["amostra"] = body[:300]
            if r.status_code == 200 and "json" in row["type"]:
                j = r.json()
                row["chaves"] = list(j.keys())[:15] if isinstance(j, dict) else f"lista[{len(j)}]"
                self.rows.append(row)
                return j
        except Exception as e:
            row["erro"] = f"{type(e).__name__}: {e}"[:200]
        self.rows.append(row)
        return None


def items(j):
    """Acha a lista de itens dentro de uma resposta da Kick."""
    if isinstance(j, list):
        return j
    if not isinstance(j, dict):
        return []
    for k in ("clips", "data", "livestreams", "items"):
        v = j.get(k)
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            inner = items(v)
            if inner:
                return inner
    return []


def g(d, *keys, default=None):
    for k in keys:
        cur = d
        for part in k.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        if cur not in (None, ""):
            return cur
    return default


def to_iso(ts):
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def norm_clip(c):
    ch = g(c, "channel.slug", "channel.username", "livestream.channel.slug", default="?")
    cid = str(g(c, "id", "clip_id", default=""))
    return {
        "platform": "kick",
        "id": "kick-" + cid,
        "kick_id": cid,
        "url": f"https://kick.com/{ch}/clips/{cid}" if cid else None,
        "video_url": g(c, "clip_url", "video_url", "video.url", "url"),
        "thumbnail_url": g(c, "thumbnail_url", "thumbnail.src", "thumbnail"),
        "broadcaster_name": g(c, "channel.username", "channel.slug", default=ch),
        "broadcaster_id": "kick:" + str(ch),
        "title": g(c, "title", default=""),
        "view_count": int(g(c, "views", "view_count", "viewer_count", default=0) or 0),
        "duration": float(g(c, "duration", default=0) or 0),
        "created_at": to_iso(g(c, "created_at", "createdAt")),
        "category": g(c, "category.slug", "category.name", default=""),
        "creator_name": g(c, "creator.username", "creator.slug", default=""),
    }


def run(cfg, start, used, blocked, langcache, detect_lang):
    slug = cfg["category_slug"]
    p = Probe()
    log = []

    # 1) lives BR da categoria
    channels = {}
    for tag, url in [
        ("lives_pt", f"{BASE}/stream/livestreams/pt?page=1&limit=50&subcategory={slug}&sort=desc"),
        ("lives_categoria_v2", f"{BASE}/api/v2/categories/{slug}/livestreams?limit=50&sort=viewers"),
        ("lives_subcategoria_v1", f"{BASE}/api/v1/subcategories/{slug}/livestreams?limit=50"),
    ]:
        for it in items(p.get(url, tag)):
            ch = g(it, "channel.slug", "slug", "channel.user.username")
            lang = str(g(it, "language", "channel.language", "livestream.language", default="")).lower()
            if ch:
                channels.setdefault(ch, {"lang": lang, "viewers": g(it, "viewer_count", "viewers", default=0)})
    # streamers da Kick ja conhecidos como BR
    for k, v in langcache.items():
        if k.startswith("kick:") and v.get("lang") == "pt":
            channels.setdefault(k[5:], {"lang": "pt", "viewers": v.get("viewers", 0)})
    log.append(f"kick: canais encontrados {len(channels)}")

    # 2) clipes: da categoria inteira e de cada canal
    raw = []
    for tag, url in [
        ("clipes_categoria_v2", f"{BASE}/api/v2/categories/{slug}/clips?sort=view&time=day"),
        ("clipes_v2_filtro", f"{BASE}/api/v2/clips?sort=view&time=day&category={slug}"),
    ]:
        raw += items(p.get(url, tag))
    chosen = sorted(channels.items(), key=lambda kv: -(kv[1].get("viewers") or 0))[: cfg.get("max_channels", 40)]
    for i, (ch, _) in enumerate(chosen):
        raw += items(p.get(f"{BASE}/api/v2/channels/{ch}/clips?sort=view&time=day", "clipes_canal" if i < 3 else "clipes_canal_"))
    # canal de controle (sabemos que existe) para separar "bloqueado" de "endereco errado"
    if not raw:
        p.get(f"{BASE}/api/v2/channels/xqc/clips?sort=view&time=day", "controle_canal")
        p.get(f"{BASE}/api/v2/channels/xqc", "controle_info_canal")

    # 3) normaliza, filtra janela/categoria e idioma
    out, seen = [], set()
    for c in raw:
        n = norm_clip(c)
        if not n["kick_id"] or n["id"] in seen or n["id"] in used.get("clips", {}):
            continue
        seen.add(n["id"])
        if n["broadcaster_name"].lower() in blocked:
            continue
        if n["created_at"] and n["created_at"] < start.strftime("%Y-%m-%dT%H:%M:%SZ"):
            continue
        if n["category"] and slug not in str(n["category"]).lower().replace(" ", "-"):
            continue
        if n["view_count"] < cfg.get("min_views", 10):
            continue
        ch = n["broadcaster_id"][5:]
        lang = channels.get(ch, {}).get("lang", "")
        cache = langcache.get(n["broadcaster_id"], {})
        if cache.get("lang") in ("pt", "en", "outro"):
            n["lang"], n["lang_src"] = cache["lang"], "streamer"
        elif lang.startswith("pt") or "portug" in lang:
            n["lang"], n["lang_src"] = "pt", "marcação da live"
        else:
            n["lang"], n["lang_src"] = "?", "sem marcação"
        out.append(n)

    # idioma pelo audio para quem nao tem marcacao (ate 15 por varredura)
    checks = 0
    for n in sorted(out, key=lambda x: -x["view_count"]):
        if n["lang"] != "?" or checks >= 15 or not n.get("video_url"):
            continue
        checks += 1
        al, prob, text = detect_lang({"url": n["video_url"]})
        if al and prob >= 0.6:
            n["lang"] = al if al in ("pt", "en") else "outro"
            n["lang_src"] = f"áudio {prob:.2f}"
            n["transcript"] = text
            langcache[n["broadcaster_id"]] = {"name": n["broadcaster_name"], "lang": n["lang"], "src": "audio"}
    for n in out:
        if n["lang"] == "pt" and n["broadcaster_id"] not in langcache:
            langcache[n["broadcaster_id"]] = {"name": n["broadcaster_name"], "lang": "pt", "src": "tag"}

    pt = [n for n in out if n["lang"] == "pt"]
    ok_tags = sorted({r["tag"] for r in p.rows if r.get("status") == 200 and r.get("chaves")})
    log.append(f"kick: clipes brutos {len(raw)}, novos válidos {len(out)}, em português {len(pt)}; "
               f"endereços que responderam: {', '.join(ok_tags) or 'nenhum'}")

    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "kick_probe.json").write_text(json.dumps(
        {"canais": dict(chosen), "tentativas": [r for r in p.rows if r["tag"] != "clipes_canal_"]
         + [r for r in p.rows if r["tag"] == "clipes_canal_"][:3]},
        ensure_ascii=False, indent=1))
    return pt, log
