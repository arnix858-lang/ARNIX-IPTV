#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Viks.tv -> IPTV M3U parser.
Extracts only direct, publicly exposed HLS/DASH URLs from channel pages.
It does not bypass logins, DRM, paywalls, tokens, or access controls.
"""
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from playwright.sync_api import sync_playwright
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from datetime import datetime, timezone

BASE = "https://live.viks.tv"
CATALOG = BASE + "/tv3/"
GUIDE = BASE + "/chto-seychas-na-tv.html"
OUT_M3U = os.getenv("VIKS_M3U", "viks.m3u")
OUT_EPG = os.getenv("VIKS_EPG", "viks_epg.xml")
MAX_CHANNELS = int(os.getenv("VIKS_MAX_CHANNELS", "180"))
DELAY = float(os.getenv("VIKS_DELAY", "0.35"))
UA = "Mozilla/5.0 (compatible; ARNIX-IPTV-ViksParser/1.0)"

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/json,*/*"})
    with urllib.request.urlopen(req, timeout=20) as response:
        return response.read().decode("utf-8", "replace"), response.geturl()

class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.media = []
        self.text = []
        self._anchor = None
        self._anchor_text = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self._anchor = (a["href"], a.get("title", ""))
            self._anchor_text = []
        if tag in ("iframe", "source", "video") or any(k in a for k in ("data-src", "data-file", "data-hls", "data-url")):
            for key in ("src", "data-src", "data-file", "data-hls", "data-url", "href"):
                if a.get(key):
                    self.media.append(a[key])
        if tag == "meta" and a.get("content"):
            self.media.append(a["content"])

    def handle_endtag(self, tag):
        if tag == "a" and self._anchor:
            href, title = self._anchor
            label = clean_name(" ".join(self._anchor_text)) or title
            self.links.append((href, label))
            self._anchor = None
            self._anchor_text = []

    def handle_data(self, data):
        if data.strip():
            self.text.append(data.strip())
            if self._anchor is not None:
                self._anchor_text.append(data.strip())

def absolute(url, base):
    url = html.unescape(url).replace("\\/", "/").replace("\\u0026", "&")
    return urllib.parse.urljoin(base, url)

def find_streams(source, page_url):
    # Only accept explicit media manifest URLs. Do not synthesize provider URLs.
    source = html.unescape(source).replace("\\/", "/").replace("\\u0026", "&")
    patterns = [
        r'https?://[^\s"\'<>\\]+?\.m3u8(?:\?[^\s"\'<>\\]*)?',
        r'https?://[^\s"\'<>\\]+?\.mpd(?:\?[^\s"\'<>\\]*)?',
        r'(?:"|\')(?:file|src|hls|stream|playlist|url)(?:"|\')\s*:\s*(?:"|\')([^"\']+\.(?:m3u8|mpd)(?:\?[^"\']*)?)(?:"|\')',
    ]
    found = []
    for pattern in patterns:
        for match in re.findall(pattern, source, flags=re.I):
            raw = match if isinstance(match, str) else match[0]
            candidate = absolute(raw, page_url)
            if urllib.parse.urlparse(candidate).scheme in ("http", "https") and candidate not in found:
                found.append(candidate)
    return found

def clean_name(value):
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip(" \t\r\n-")

def capture_dynamic_streams(channel_urls):
    """
    Open channel pages in Chromium and record publicly requested HLS/DASH manifests.
    This observes normal page loading; it does not bypass login, DRM, or access controls.
    """
    result = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=UA,
                viewport={"width": 1280, "height": 720},
                ignore_https_errors=False,
            )
            page = context.new_page()
            for index, url in enumerate(channel_urls, 1):
                found = []

                def remember(request):
                    target = request.url
                    if re.search(r"\\.(?:m3u8|mpd)(?:\\?|$)", target, re.I):
                        if target not in found:
                            found.append(target)

                page.on("request", remember)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(2500)
                    # A normal play attempt may be needed before the player requests its manifest.
                    try:
                        page.locator("video").first.evaluate(
                            "(v) => { v.muted = true; return v.play().catch(() => null); }",
                            timeout=1500,
                        )
                        page.wait_for_timeout(2000)
                    except Exception:
                        pass
                except Exception as exc:
                    print(f"Browser capture [{index}/{len(channel_urls)}]: {exc}", file=sys.stderr)
                finally:
                    page.remove_listener("request", remember)
                if found:
                    result[url] = found
                    print(f"Browser capture [{index}/{len(channel_urls)}]: {len(found)} manifest(s)")
            context.close()
            browser.close()
    except Exception as exc:
        print("Playwright capture unavailable:", exc, file=sys.stderr)
    return result

def discover_channels():
    body, final_url = get(CATALOG)
    parser = PageParser()
    parser.feed(body)
    channels, seen = [], set()
    for href, title in parser.links:
        url = absolute(href, final_url)
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc not in ("live.viks.tv", "www.live.viks.tv"):
            continue
        if not parsed.path.lower().endswith(".html") or parsed.path.rstrip("/") == "/tv3":
            continue
        if url in seen:
            continue
        name = clean_name(title)
        if not name:
            # use URL slug as a last resort
            name = clean_name(parsed.path.rsplit("/", 1)[-1].removesuffix(".html").replace("-", " "))
        if name:
            channels.append({"name": name, "page": url})
            seen.add(url)
        if len(channels) >= MAX_CHANNELS:
            break
    return channels

def category(name):
    n = name.lower()
    rules = [
        (("спорт", "матч", "sport", "евроспорт", "setanta", "xsport", "кхл"), "Спорт"),
        (("мульт", "дет", "nick", "disney", "tiji", "ani", "gulli", "том и джерри"), "Детские"),
        (("муз", "music", "mtv", "bridge", "ru tv", "европа плюс"), "Музыка"),
        (("новост", "news", "дождь", "рбк", "россия 24", "bbc"), "Новости"),
        (("кино", "фильм", "movie", "cinema", "hollywood", "премьера", "комедия"), "Фильмы и сериалы"),
        (("discovery", "history", "national geographic", "planet", "animal", "наука", "travel"), "Познавательные"),
    ]
    for words, group in rules:
        if any(w in n for w in words):
            return group
    return "Развлекательные"

def parse_schedule():
    # The guide is parsed only when the page actually contains programme data.
    # Viks may show "Программа для этого телеканала отсутствует"; such entries are skipped.
    try:
        body, _ = get(GUIDE)
    except Exception as exc:
        print("EPG page unavailable:", exc, file=sys.stderr)
        return []
    programs = []
    # Support JSON-LD BroadcastEvent objects where provided.
    for block in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', body, re.I | re.S):
        try:
            data = json.loads(html.unescape(block))
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                if item.get("@type") == "BroadcastEvent":
                    start = item.get("startDate")
                    end = item.get("endDate")
                    name = item.get("name")
                    channel = item.get("publishedOn", {})
                    channel_name = channel.get("name", "") if isinstance(channel, dict) else ""
                    if start and end and name:
                        programs.append((channel_name, name, start, end))
                stack.extend(v for v in item.values() if isinstance(v, (dict, list)))
            elif isinstance(item, list):
                stack.extend(item)
    return programs

def write_epg(programs):
    if not programs:
        if os.path.exists(OUT_EPG):
            os.remove(OUT_EPG)
        print("EPG: сайт не предоставил распознаваемую телепрограмму; XMLTV не создан.")
        return False
    tv = ET.Element("tv", {"generator-info-name": "ARNIX IPTV Viks parser"})
    channel_ids = {}
    for channel, _, _, _ in programs:
        if channel:
            channel_ids.setdefault(channel, re.sub(r"[^a-z0-9]+", ".", channel.lower()).strip(".") + ".viks")
    for name, cid in channel_ids.items():
        ch = ET.SubElement(tv, "channel", {"id": cid})
        ET.SubElement(ch, "display-name").text = name
    for channel, title, start, end in programs:
        cid = channel_ids.get(channel)
        if not cid:
            continue
        def stamp(value):
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.strftime("%Y%m%d%H%M%S %z")
        p = ET.SubElement(tv, "programme", {"channel": cid, "start": stamp(start), "stop": stamp(end)})
        ET.SubElement(p, "title", {"lang": "ru"}).text = title
    ET.indent(tv, space="  ")
    ET.ElementTree(tv).write(OUT_EPG, encoding="utf-8", xml_declaration=True)
    return True

def main():
    print("Получаю список каналов:", CATALOG)
    channels = discover_channels()
    print("Найдено страниц каналов:", len(channels))
    dynamic_by_page = capture_dynamic_streams([channel["page"] for channel in channels])
    entries, seen_streams = [], set()
    for index, channel in enumerate(channels, 1):
        try:
            body, final_url = get(channel["page"])
            parser = PageParser()
            parser.feed(body)
            candidates = list(parser.media)
            # Some sites put player configuration in inline scripts/JSON.
            candidates.extend(find_streams(body, final_url))
            for candidate in list(candidates):
                candidate_url = absolute(candidate, final_url)
                if urllib.parse.urlparse(candidate_url).netloc in ("live.viks.tv", "www.live.viks.tv"):
                    try:
                        embedded, embedded_url = get(candidate_url)
                        candidates.extend(find_streams(embedded, embedded_url))
                    except Exception:
                        pass
            streams = []
            for candidate in candidates:
                streams.extend(find_streams(candidate, final_url))
                if re.search(r"\.(m3u8|mpd)(?:\?|$)", candidate, re.I):
                    streams.append(absolute(candidate, final_url))
            streams.extend(dynamic_by_page.get(channel["page"], []))
            streams = list(dict.fromkeys(s for s in streams if s.startswith(("http://", "https://"))))
            if streams:
                for number, stream in enumerate(streams, 1):
                    if stream in seen_streams:
                        continue
                    seen_streams.add(stream)
                    name = channel["name"] if number == 1 else f'{channel["name"]} ({number})'
                    entries.append((name, stream, category(name)))
                print(f"[{index}/{len(channels)}] OK: {channel['name']} — {len(streams)} поток(ов)")
            else:
                print(f"[{index}/{len(channels)}] Нет открытого прямого потока: {channel['name']}")
        except Exception as exc:
            print(f"[{index}/{len(channels)}] Ошибка {channel['name']}: {exc}", file=sys.stderr)
        time.sleep(DELAY)

    if not entries:
        print("Не найдено прямых публичных HLS/DASH ссылок. Старый плейлист не перезаписываю.", file=sys.stderr)
        return 2

    lines = ["#EXTM3U"]
    for name, stream, group in entries:
        safe_name = name.replace('"', "'").replace(",", " ")
        lines.append(f'#EXTINF:-1 group-title="{group}",{safe_name}')
        lines.append(stream)
    with open(OUT_M3U, "w", encoding="utf-8", newline="\n") as file:
        file.write("\n".join(lines) + "\n")

    epg_ok = write_epg(parse_schedule())
    print(f"Готово: {len(entries)} потоков -> {OUT_M3U}")
    if epg_ok:
        print(f"EPG сохранён -> {OUT_EPG}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
