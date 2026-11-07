#!/usr/bin/env python3
"""Conservatively remove only HTTP(S) playlist entries that return 404 or 410.
UDP/multicast and temporary/network errors are left untouched.
"""
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PLAYLIST = Path("playlist.m3u")
TIMEOUT = 15

def is_confirmed_dead(url: str) -> bool:
    if not url.lower().startswith(("http://", "https://")):
        return False
    req = Request(url, headers={
        "User-Agent": "ARNIX-IPTV-Playlist-Monitor/1.0",
        "Range": "bytes=0-1023",
    })
    try:
        with urlopen(req, timeout=TIMEOUT) as response:
            # A successful response, including a redirect, is not considered dead.
            return False
    except HTTPError as exc:
        # Only permanent missing-resource responses are safe to prune.
        return exc.code in (404, 410)
    except (URLError, TimeoutError, OSError):
        # Network failures are inconclusive; never delete on a transient failure.
        return False

def main() -> None:
    if not PLAYLIST.exists():
        raise SystemExit("playlist.m3u not found")
    lines = PLAYLIST.read_text(encoding="utf-8-sig").splitlines()
    kept = []
    removed = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#EXTINF") and i + 1 < len(lines):
            stream = lines[i + 1].strip()
            if is_confirmed_dead(stream):
                removed.append((line.rsplit(",", 1)[-1].strip(), stream))
                i += 2
                # Also discard any immediately following per-entry metadata lines.
                while i < len(lines) and lines[i].startswith(("#EXTVLCOPT:", "#KODIPROP:", "#EXTHTTP:")):
                    i += 1
                continue
            kept.append(line)
            kept.append(lines[i + 1])
            i += 2
            continue
        kept.append(line)
        i += 1

    if removed:
        PLAYLIST.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")
        print(f"Removed {len(removed)} confirmed dead channel(s):")
        for name, url in removed:
            print(f"- {name}: {url}")
    else:
        print("No confirmed 404/410 streams found; playlist unchanged.")

if __name__ == "__main__":
    main()
