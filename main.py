"""Cosmic Tracker server (FastAPI): serves index.html and relays requests to NASA/IPAC IRSA and CDS.
Local run:  uvicorn main:app --port 8765     then open  http://localhost:8765
Diagnose:   /test
"""
import json, os, ssl, threading, time
import urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET

from fastapi import FastAPI
from fastapi.responses import FileResponse, PlainTextResponse, Response

ALLOWED = ("irsa.ipac.caltech.edu", "cds.unistra.fr")
SIA = "https://irsa.ipac.caltech.edu/SIA"
COLLECTIONS = ["spherex_dr1", "spherex_qr3", "spherex_qr2", "spherex_qr"]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"}
HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_EXT = (".html", ".css", ".js", ".json", ".png", ".jpg", ".jpeg", ".svg", ".ico", ".woff2", ".webp")

GATE = threading.Semaphore(4)      # never hit IRSA with more than 4 requests at once
CACHE, CACHE_MAX = {}, 300         # small in-memory cache (url -> (type, bytes))
LOCK = threading.Lock()

app = FastAPI(title="Cosmic Tracker")


def _open(url, timeout):
    req = urllib.request.Request(url, headers=UA)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except Exception as e:
        reason = getattr(e, "reason", None)
        if isinstance(reason, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(e):
            return urllib.request.urlopen(req, timeout=timeout, context=ssl._create_unverified_context())
        raise


def fetch_bytes(url, timeout=60, tries=3):
    """Download with retries and backoff (handles 429/5xx and timeouts). Returns (content_type, data)."""
    with LOCK:
        if url in CACHE:
            return CACHE[url]
    last = None
    for n in range(tries):
        try:
            with GATE:
                with _open(url, timeout) as r:
                    res = (r.headers.get("Content-Type", "application/octet-stream"), r.read())
            with LOCK:
                if len(CACHE) >= CACHE_MAX:
                    CACHE.pop(next(iter(CACHE)))
                CACHE[url] = res
            return res
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (429, 500, 502, 503, 504):
                raise
        except Exception as e:
            last = e
        time.sleep(1.5 * (n + 1))
    raise last


def tag(e):
    return e.tag.split("}")[-1]


def run_test():
    out = []
    log = out.append
    try:
        for col in COLLECTIONS:
            url = SIA + "?COLLECTION=" + col + "&POS=circle+269.4521+4.6934+0.0003"
            try:
                ctype, data = fetch_bytes(url, tries=2)
            except Exception as e:
                log("[%s] request failed: %s" % (col, repr(e)[:200]))
                continue
            root = ET.fromstring(data)
            fields = [f.get("name") for f in root.iter() if tag(f) == "FIELD"]
            rows = [t for t in root.iter() if tag(t) == "TR"]
            log("[%s] HTTP ok, %d bytes, %d images" % (col, len(data), len(rows)))
            if rows and "access_url" in fields:
                tds = [td.text or "" for td in rows[0] if tag(td) == "TD"]
                img = tds[fields.index("access_url")].strip()
                cut = img + ("&" if "?" in img else "?") + "center=269.4521,4.6934d&size=0.2"
                _, b = fetch_bytes(cut)
                log("    cutout: %d bytes, starts with %r" % (len(b), b[:6]))
                log("RESULT: everything works with " + col)
                return "\n".join(out)
        log("RESULT: NASA answered, but no usable images. Check the lines above.")
    except Exception as e:
        log("FAILED: " + repr(e)[:500])
    return "\n".join(out)


# Plain `def` routes run in FastAPI's thread pool, so blocking downloads don't freeze the server.
@app.get("/test")
def test():
    text = run_test()
    print(text)
    return PlainTextResponse(text)


@app.get("/api/collections")
def collections():
    return Response(json.dumps({"collections": COLLECTIONS}), media_type="application/json")


@app.get("/proxy")
def proxy(u: str = ""):
    p = urllib.parse.urlparse(u)
    if p.scheme != "https" or p.hostname not in ALLOWED:
        return PlainTextResponse("host not allowed", status_code=403)
    try:
        ctype, data = fetch_bytes(u)
        return Response(content=data, media_type=ctype)
    except Exception as e:
        msg = repr(e).replace("\n", " ")[:300]
        print("PROXY ERROR:", msg, "| URL:", u[:200])
        return PlainTextResponse(msg, status_code=502)


@app.get("/")
def home():
    return FileResponse(os.path.join(HERE, "index.html"))


@app.get("/{name}")
def static_file(name: str):
    """Serve other local files next to main.py (css, js, images) but never source code."""
    path = os.path.join(HERE, os.path.basename(name))
    if name.lower().endswith(STATIC_EXT) and os.path.isfile(path):
        return FileResponse(path)
    return PlainTextResponse("not found", status_code=404)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8765))
    print("Cosmic Tracker server. Open: http://localhost:%d  (self-test: /test)" % port)
    uvicorn.run(app, host="0.0.0.0", port=port)
