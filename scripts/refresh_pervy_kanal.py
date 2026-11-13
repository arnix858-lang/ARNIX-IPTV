import html
import re
import requests
from urllib.parse import urljoin

PAGE = "https://smotrettv.net/tv/public/1003-pervyj-kanal.html"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Referer": PAGE,
}

def main():
    page = requests.get(PAGE, headers=HEADERS, timeout=30)
    page.raise_for_status()
    source = html.unescape(page.text)

    # Extract the current, time-limited HLS URL from the public page.
    matches = re.findall(
        r'https?://server\.musical\.uz/tv/1tv/index\.m3u8[^"\'\\s<>]*',
        source,
        flags=re.I,
    )
    if not matches:
        raise RuntimeError("No HLS URL found on SmotretTV page")

    stream_url = matches[0].replace("\\/", "/")
    check = requests.get(stream_url, headers=HEADERS, timeout=30)
    check.raise_for_status()
    if "#EXTM3U" not in check.text:
        raise RuntimeError("The extracted URL did not return an HLS manifest")

    # Verify a nested media playlist when the response is a master playlist.
    variants = [
        line.strip() for line in check.text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if variants:
        child_url = urljoin(check.url, variants[0])
        child = requests.get(child_url, headers=HEADERS, timeout=30)
        child.raise_for_status()
        if "#EXTM3U" not in child.text:
            raise RuntimeError("Nested playlist is not valid HLS")

    with open("PERVY_KANAL.m3u", "w", encoding="utf-8", newline="\n") as f:
        f.write("#EXTM3U\n")
        f.write('#EXTINF:-1 tvg-name="Первый канал" group-title="Россия",Первый канал\n')
        f.write(stream_url + "\n")

    print("Playlist refreshed successfully")

if __name__ == "__main__":
    main()
