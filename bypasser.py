import httpx, re, asyncio, random
from urllib.parse import urlparse, unquote

UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
]

def headers(referer: str = None) -> dict:
    h = {
        "User-Agent": random.choice(UA_LIST),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
    }
    if referer:
        h["Referer"] = referer
    return h


# ---------- Layer 1: Simple redirect chain ----------
async def redirect_bypass(client: httpx.AsyncClient, url: str) -> str:
    current = url
    for _ in range(12):
        r = await client.get(current)
        if r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers.get("location", "")
            if not loc:
                break
            current = loc if loc.startswith("http") else f"{urlparse(str(r.url)).scheme}://{urlparse(str(r.url)).netloc}{loc}"
            continue
        html = r.text
        # meta refresh
        m = re.search(r'http-equiv=["\']refresh["\'][^>]*url=([^"\'>]+)', html, re.I)
        if m:
            current = unquote(m.group(1).strip()); continue
        # window.location / location.href / location.replace
        m = re.search(r'(?:window\.)?location(?:\.href)?\s*=\s*["\']([^"\']+)["\']|location\.replace\(["\']([^"\']+)["\']\)', html, re.I)
        if m:
            current = (m.group(1) or m.group(2)).strip(); continue
        return current
    return current


# ---------- Layer 2: Universal form bypass (40+ Indian shorteners) ----------
# linkpays, earn4link, sfl.gl, urlshortx, liteurl, get2short, short4cash,
# safelink, linksgo, intercelestial, bindaaslinks, instantlinks, linkflys,
# greenmotors, tw4all, unlocktoearn, pahe.plus, remso, nazki, oii, tii.ai ...
# Sab isi AdLinkFly/SharedCash pattern pe chalte hain.
async def form_bypass(client: httpx.AsyncClient, url: str) -> str:
    r = await client.get(url)
    html = r.text

    # form inputs collect karo (hidden token fields included)
    inputs = {}
    for name, value in re.findall(r'<input[^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']', html):
        inputs[name] = value
    # value pehle ho to bhi pakad lo (attribute order swap)
    for name, value in re.findall(r'<input[^>]*value=["\']([^"\']*)["\'][^>]*name=["\']([^"\']+)["\']', html):
        inputs[value] = name if value not in inputs else inputs[value]
        inputs[name] = value

    action = re.search(r'<form[^>]*action=["\']([^"\']*)["\']', html, re.I)
    base = "{0.scheme}://{0.netloc}".format(urlparse(str(r.url)))
    endpoint = action.group(1) if action else "/links/go"
    if endpoint.startswith("//"):
        endpoint = urlparse(str(r.url)).scheme + ":" + endpoint
    elif not endpoint.startswith("http"):
        endpoint = base + endpoint

    # anti-bot countdown respect karo (5-15s random)
    cd = re.search(r'(?:countdown|timer|wait)\s*=?\s*(\d+)', html, re.I)
    wait = min(int(cd.group(1)) if cd else 8, 15)
    await asyncio.sleep(wait)

    r2 = await client.post(endpoint, data=inputs, headers={
        **headers(referer=str(r.url)), "X-Requested-With": "XMLHttpRequest",
        "Origin": base, "Content-Type": "application/x-www-form-urlencoded",
    })
    try:
        j = r2.json()
    except Exception:
        raise ValueError(f"form POST not JSON (status {r2.status_code})")

    # har possible JSON shape handle karo
    for key in ("url", "link", "data", "redirect", "href", "result"):
        v = j.get(key)
        if isinstance(v, str) and v.startswith("http"):
            return unquote(v)
        if isinstance(v, dict):
            for k2 in ("url", "link", "href"):
                if isinstance(v.get(k2), str) and v[k2].startswith("http"):
                    return v[k2]
    # fallback: koi bhi http value
    for v in j.values():
        if isinstance(v, str) and v.startswith("http"):
            return v
    raise ValueError(f"form bypass no URL in response: {j}")


# ---------- Layer 3: HTML link extraction (last resort) ----------
async def extract_html_links(client: httpx.AsyncClient, url: str) -> list:
    r = await client.get(url)
    links = re.findall(r'href=["\'](https?://[^"\']+)["\']', r.text)
    # junk filter: apne domain pe wapas point karne wale hatao
    host = urlparse(url).netloc
    return [l for l in links if host not in l and not any(
        x in l for x in ("facebook", "twitter", "telegram.me/js", "google", "youtube"))][:5]


async def bypass_engine(url: str, domain_router) -> dict:
    """Master engine — layers ko order mein try karta hai."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    host = urlparse(url).netloc.lower()

    async with httpx.AsyncClient(
        headers=headers(), timeout=30, follow_redirects=True,
        max_redirects=15, http2=True,
    ) as client:
        errors = []

        # Layer 2 pehle agar ye form-based domain hai
        if domain_router.is_form_domain(host):
            try:
                result = await form_bypass(client, url)
                return {"bypassed": result, "via": "form", "host": host}
            except Exception as e:
                errors.append(f"form: {e}")

        # PyBypass (GP, gplinks, droplink, adfly, bit.ly, ouo, etc.)
        try:
            import PyBypass as pb
            result = pb.bypass(url)
            if result and result.startswith("http"):
                return {"bypassed": result, "via": "pybypass", "host": host}
        except Exception as e:
            errors.append(f"pybypass: {e}")

        # Layer 1: redirect chain
        try:
            result = await redirect_bypass(client, url)
            if result != url and result.startswith("http"):
                return {"bypassed": result, "via": "redirect", "host": host}
            errors.append("redirect: no change")
        except Exception as e:
            errors.append(f"redirect: {e}")

        # Layer 3: HTML extraction (candidates return karo)
        try:
            links = await extract_html_links(client, url)
            if links:
                return {"bypassed": links[0], "candidates": links, "via": "html", "host": host}
        except Exception as e:
            errors.append(f"html: {e}")

        raise RuntimeError("; ".join(errors) or "all layers failed")
