from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import time, asyncio
from urllib.parse import urlparse
from bypasser import bypass_engine
import httpx

app = FastAPI(title="Universal Bypass API", version="2.0")

# ---------- Config ----------
CACHE_TTL = 600          # 10 min cache
RATE_LIMIT = 15          # requests per 60s per IP
FORM_DOMAINS = [
    "linkpays", "earn4link", "sfl.gl", "urlshortx", "liteurl", "get2short",
    "short4cash", "safelink", "linksgo", "intercelestial", "bindaaslinks",
    "instantlinks", "linkflys", "greenmotors", "tw4all", "unlocktoearn",
    "pahe.plus", "remso", "nazki", "oii", "tpi", "tii.ai", "gkyfilehost",
]

class DomainRouter:
    def is_form_domain(self, host: str) -> bool:
        return any(d in host for d in FORM_DOMAINS)

router = DomainRouter()
cache: dict = {}
rate_map: dict = {}

def rate_limited(ip: str) -> bool:
    now = time.time()
    hits = [t for t in rate_map.get(ip, []) if now - t < 60]
    hits.append(now)
    rate_map[ip] = hits
    return len(hits) > RATE_LIMIT

def cache_get(key: str):
    v = cache.get(key)
    if v and time.time() - v[1] < CACHE_TTL:
        return v[0]
    return None

# ---------- Endpoints ----------

@app.get("/bypass")
async def bypass(url: str, request: Request):
    ip = request.client.host
    if rate_limited(ip):
        raise HTTPException(429, "Rate limit exceeded, try again in a minute")
    key = url.lower()
    cached = cache_get(key)
    if cached:
        return {**cached, "cached": True}
    try:
        result = await asyncio.wait_for(bypass_engine(url, router), timeout=45)
        cache[key] = (result, time.time())
        return {**result, "success": True}
    except asyncio.TimeoutError:
        raise HTTPException(504, "Bypass timed out")
    except Exception as e:
        raise HTTPException(500, str(e))


class BatchRequest(BaseModel):
    urls: list[str]

@app.post("/bypass/batch")
async def bypass_batch(body: BatchRequest):
    if len(body.urls) > 10:
        raise HTTPException(400, "Max 10 URLs per batch")
    async def one(u):
        try:
            r = await asyncio.wait_for(bypass_engine(u, router), timeout=45)
            return {"url": u, "success": True, **r}
        except Exception as e:
            return {"url": u, "success": False, "error": str(e)}
    results = await asyncio.gather(*[one(u) for u in body.urls])
    return {"results": results}


@app.get("/health")
async def health():
    return {"status": "ok", "cache_size": len(cache)}


@app.exception_handler(Exception)
async def all_errors(request, exc):
    return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})
