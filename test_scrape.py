import httpx
import re

url = "https://www.tiktok.com/@cookingwithayeh/video/7311057723498599723"
headers = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                   "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                   "Version/17.0 Mobile/15E148 Safari/604.1",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}

r = httpx.get(url, headers=headers, follow_redirects=True, timeout=30)
print("Status:", r.status_code)
print("URL:", r.url)
print("HTML length:", len(r.text))

# og:description
m = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']*)["\']', r.text, re.IGNORECASE)
if not m:
    m = re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']og:description["\']', r.text, re.IGNORECASE)
if m:
    print("OG DESC:", m.group(1)[:300])
else:
    print("No og:description found")

# og:title
m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']*)["\']', r.text, re.IGNORECASE)
if not m:
    m = re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']og:title["\']', r.text, re.IGNORECASE)
if m:
    print("OG TITLE:", m.group(1)[:200])
else:
    print("No og:title found")

# <title>
m = re.search(r'<title[^>]*>([^<]+)</title>', r.text, re.IGNORECASE)
if m:
    print("TITLE:", m.group(1)[:200])

# Also try Instagram
print("\n--- Instagram ---")
url2 = "https://www.instagram.com/reel/DFzIYXwoVjx/"
r2 = httpx.get(url2, headers=headers, follow_redirects=True, timeout=30)
print("Status:", r2.status_code)

m = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']*)["\']', r2.text, re.IGNORECASE)
if not m:
    m = re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']og:description["\']', r2.text, re.IGNORECASE)
if m:
    print("OG DESC:", m.group(1)[:300])
else:
    print("No og:description")

m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']*)["\']', r2.text, re.IGNORECASE)
if not m:
    m = re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']og:title["\']', r2.text, re.IGNORECASE)
if m:
    print("OG TITLE:", m.group(1)[:200])
