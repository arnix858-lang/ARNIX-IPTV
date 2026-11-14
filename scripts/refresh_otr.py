#!/usr/bin/env python3
"""Extract the official OTR live HLS stream and update only its entry in playlist.m3u."""
import html
import re
from urllib.parse import urljoin, urlparse

import requests

PAGE = "https://otr-online.ru/online/"
TIMEOUT = 25
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Referer": PAGE,
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

def normalize(text):
    return html.unescape(text).replace("\\/", "/").replace("\\u0026", "&")

def candidate_urls(text, base):
    text = normalize(text)
    found = []
    # Direct HLS links embedded in page markup, JSON or player scripts.
    for match in re.findall(r'https?://[^\s"\'<>\\]+?\.m3u8(?:\?[^\s"\'<>\\]*)?', text, re.I):
        found.append(match.rstrip("),;"))
    # Relative HLS URLs.
    for match in re.findall(r'["\']([^"\']+?\.m3u8(?:\?[^"\']*)?)["\']', text, re.I):
        found.append(urljoin(base, match))
    # Player iframe and script resources may contain the stream URL.
    for match in re.findall(r'<(?:iframe|script)[^>]+(?:src)=["\']([^"\']+)["\']', text, re.I):
        found.append(urljoin(base, match))
    return list(dict.fromkeys(found))

def fetch_text(url):
    response = SESSION.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    return response.url, response.text

def is_otr_host(url):
    host = (urlparse(url).hostname or "").lower()
    return host == "otr-online.ru" or host.endswith(".otr-online.ru")

def main():
    visited = set()
    queue = [PAGE]
    stream = None

    # Inspect the live page and same-site player/script resources.
    while queue and len(visited) < 20 and not stream:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        final_url, body = fetch_text(url)
        for candidate in candidate_urls(body, final_url):
            if candidate.lower().split("?", 1)[0].endswith(".m3u8"):
                if not stream:
                    stream = candidate
            elif candidate.startswith("http") and is_otr_host(candidate) and candidate not in visited:
                if len(queue) < 20:
                    queue.append(candidate)

    if not stream:
        raise RuntimeError("Official OTR page did not expose an HLS .m3u8 URL; playlist left unchanged")

    manifest = SESSION.get(stream, timeout=TIMEOUT)
    manifest.raise_for_status()
    if "#EXTM3U" not in manifest.text[:4096]:
        raise RuntimeError("Extracted OTR URL is not a valid HLS manifest; playlist left unchanged")

    path = "playlist.m3u"
    with open(path, "r", encoding="utf-8-sig") as file:
        lines = file.read().splitlines()

    entry_index = next((i for i, line in enumerate(lines)
                        if line.startswith("#EXTINF") and
                        ('tvg-id="otr"' in line.lower() or line.rstrip().endswith(",ОТР"))), None)
    if entry_index is None or entry_index + 1 >= len(lines):
        raise RuntimeError("OTR entry was not found in playlist.m3u")

    lines[entry_index + 1] = stream
    with open(path, "w", encoding="utf-8", newline="\n") as file:
        file.write("\n".join(lines).rstrip() + "\n")

    print("OTR HLS stream verified and playlist entry updated:", stream)

if __name__ == "__main__":
    main()
