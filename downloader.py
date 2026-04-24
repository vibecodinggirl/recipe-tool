"""
Video Downloader - Lädt Audio, Caption, Untertitel und Frames aus Instagram Reels & TikTok Videos.
Nutzt yt-dlp als Backend, mit HTML-Scraping als Fallback.
"""

import os
import re
import uuid
import json
import subprocess
import logging

import httpx

logger = logging.getLogger(__name__)

ALLOWED_DOMAINS = [
    "instagram.com",
    "www.instagram.com",
    "tiktok.com",
    "www.tiktok.com",
    "vm.tiktok.com",
]

TMP_DIR = "tmp"


def _validate_url(url: str) -> None:
    """Prüft ob die URL von einer erlaubten Plattform stammt."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if not any(hostname == domain or hostname.endswith("." + domain) for domain in ALLOWED_DOMAINS):
        raise ValueError(
            f"URL muss von Instagram oder TikTok sein. Erhalten: {hostname}"
        )


def download_video_data(url: str, fast_mode: bool = False) -> dict:
    """
    Lädt Audio + Metadaten (Caption) aus einem Video.
    fast_mode=True: Nur Metadaten + Untertitel (kein Audio/Video-Download → viel schneller)
    """
    _validate_url(url)

    os.makedirs(TMP_DIR, exist_ok=True)
    file_id = uuid.uuid4().hex[:12]

    result = {
        "audio_path": None,
        "caption": "",
        "title": "",
        "frames_dir": None,
        "subtitles": "",
    }

    # --- 1. oEmbed API (schnell & zuverlässig, wird nicht blockiert!) ---
    oembed_caption, oembed_title = _fetch_oembed(url)
    result["caption"] = oembed_caption
    result["title"] = oembed_title

    oembed_has_data = bool(result["caption"] or result["title"])

    # --- 2. Falls oEmbed leer: Fallback auf yt-dlp + HTML ---
    if not oembed_has_data:
        logger.info("oEmbed leer — versuche yt-dlp/HTML Fallback...")
        result["caption"], result["title"] = _fetch_metadata(url)

    # --- 3. Untertitel nur wenn oEmbed leer war (sonst unnötig langsam) ---
    if not oembed_has_data:
        try:
            result["subtitles"] = _download_subtitles(url, file_id)
        except Exception as e:
            logger.warning(f"Untertitel fehlgeschlagen: {e}")

    # Im Fast-Mode überspringen wir Audio + Frames (spart 30-60 Sekunden)
    if not fast_mode:
        # --- 4. Audio herunterladen ---
        try:
            result["audio_path"] = _download_audio(url, file_id)
        except Exception as e:
            logger.warning(f"Audio-Download fehlgeschlagen: {e}")

        # --- 5. Video-Frames extrahieren (für OCR) ---
        try:
            result["frames_dir"] = _extract_frames(url, file_id)
        except Exception as e:
            logger.warning(f"Frame-Extraktion fehlgeschlagen: {e}")
    else:
        logger.info("Fast-Mode: Überspringe Audio + Frame-Download")

    # Mindestens Caption oder Untertitel müssen vorhanden sein
    has_content = (
        result["caption"] or result["title"] or
        result["subtitles"] or result["audio_path"]
    )
    if not has_content:
        raise RuntimeError(
            "Konnte keine Infos aus dem Video holen. "
            "Instagram braucht manchmal einen Login — "
            "versuche den Link nochmal oder nutze ein TikTok-Video."
        )

    return result


def _resolve_short_url(url: str) -> str:
    """Löst Kurz-URLs (vm.tiktok.com) auf — per HTTP-Redirect oder yt-dlp."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    hostname = parsed.hostname or ""

    # Nur vm.tiktok.com Kurz-URLs müssen aufgelöst werden
    if hostname != "vm.tiktok.com":
        return url

    logger.info(f"Löse Kurz-URL auf: {url}")

    # Methode 1: HTTP HEAD/GET mit Redirect
    for method in ("HEAD", "GET"):
        try:
            if method == "HEAD":
                r = httpx.head(url, follow_redirects=True, timeout=10.0,
                               headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"})
            else:
                r = httpx.get(url, follow_redirects=True, timeout=10.0,
                              headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"})
            resolved = str(r.url)
            # Nur akzeptieren wenn es eine echte Video-URL ist (nicht tiktok.com Startseite)
            if "tiktok.com" in resolved and "/video/" in resolved:
                clean = resolved.split("?")[0]
                logger.info(f"Aufgelöst ({method}): {url} → {clean}")
                return clean
        except Exception as e:
            logger.warning(f"Kurz-URL {method} fehlgeschlagen: {e}")

    # Methode 2: yt-dlp --print webpage_url (kann JS-Redirects auflösen)
    try:
        cmd = [
            "yt-dlp",
            "--no-playlist",
            "--skip-download",
            "--print", "webpage_url",
            "--no-warnings",
            "--quiet",
            "--no-check-certificates",
            "--user-agent", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
            url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0 and result.stdout.strip():
            resolved = result.stdout.strip().split("?")[0]
            if "tiktok.com" in resolved and "/video/" in resolved:
                logger.info(f"Aufgelöst (yt-dlp): {url} → {resolved}")
                return resolved
    except Exception as e:
        logger.warning(f"yt-dlp URL-Auflösung fehlgeschlagen: {e}")

    logger.warning(f"Konnte Kurz-URL nicht auflösen: {url}")
    return url


def _fetch_oembed(url: str) -> tuple[str, str]:
    """Holt Caption/Titel via oEmbed API (offizielle, öffentliche API — wird nicht blockiert!)."""
    from urllib.parse import quote

    # Kurz-URLs zuerst auflösen — oEmbed braucht die volle URL!
    resolved_url = _resolve_short_url(url)

    if "tiktok.com" in resolved_url:
        oembed_url = f"https://www.tiktok.com/oembed?url={quote(resolved_url, safe='')}"
    elif "instagram.com" in resolved_url:
        oembed_url = f"https://api.instagram.com/oembed/?url={quote(resolved_url, safe='')}&omitscript=true"
    else:
        return "", ""

    logger.info(f"Fetching oEmbed: {oembed_url}")

    try:
        r = httpx.get(oembed_url, follow_redirects=True, timeout=10.0,
                      headers={"User-Agent": "Mozilla/5.0"})
    except Exception as e:
        logger.warning(f"oEmbed request failed: {e}")
        return "", ""

    if r.status_code != 200:
        logger.warning(f"oEmbed returned {r.status_code}")
        return "", ""

    try:
        data = r.json()
    except Exception:
        logger.warning("oEmbed response is not JSON")
        return "", ""

    caption = data.get("title", "")
    author = data.get("author_name", "")
    title = f"{caption[:80]} — @{author}" if author else caption[:80]

    logger.info(f"oEmbed: Caption ({len(caption)} chars), Author: {author}")
    return caption, title


def _fetch_metadata(url: str) -> tuple[str, str]:
    """Holt Caption/Beschreibung und Titel des Videos. yt-dlp zuerst, dann HTML-Fallback."""
    caption, title = _fetch_metadata_ytdlp(url)

    # Fallback: HTML Meta-Tags direkt scrapen
    if not caption and not title:
        logger.info("yt-dlp Metadaten leer — versuche HTML-Fallback...")
        caption, title = _fetch_metadata_html(url)

    return caption, title


def _fetch_metadata_ytdlp(url: str) -> tuple[str, str]:
    """Holt Metadaten via yt-dlp."""
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--skip-download",
        "--dump-json",
        "--no-warnings",
        "--quiet",
        "--no-check-certificates",
        "--user-agent", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        url,
    ]

    logger.info(f"Fetching metadata via yt-dlp: {url}")

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        logger.warning("yt-dlp metadata timed out")
        return "", ""

    if proc.returncode != 0:
        logger.warning(f"Metadata fetch failed: {proc.stderr}")
        return "", ""

    try:
        info = json.loads(proc.stdout)
    except json.JSONDecodeError:
        logger.warning("Could not parse video metadata JSON")
        return "", ""

    caption = info.get("description") or ""
    title = info.get("title") or info.get("fulltitle") or ""

    logger.info(f"Caption ({len(caption)} chars): {caption[:100]}...")
    return caption, title


def _fetch_metadata_html(url: str) -> tuple[str, str]:
    """
    Fallback: Holt Caption/Titel aus HTML Meta-Tags (og:description, og:title).
    Funktioniert auch wenn yt-dlp blockiert wird.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                       "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                       "Version/17.0 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    }

    try:
        response = httpx.get(url, headers=headers, follow_redirects=True, timeout=30.0)
        html = response.text
    except Exception as e:
        logger.warning(f"HTML fetch failed: {e}")
        return "", ""

    caption = ""
    title = ""

    # og:description
    match = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']*)["\']', html, re.IGNORECASE)
    if not match:
        match = re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']og:description["\']', html, re.IGNORECASE)
    if match:
        caption = match.group(1)
        # HTML entities decodieren
        caption = caption.replace("&amp;", "&").replace("&#x27;", "'").replace("&quot;", '"').replace("&#39;", "'").replace("&lt;", "<").replace("&gt;", ">")

    # og:title
    match = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']*)["\']', html, re.IGNORECASE)
    if not match:
        match = re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']og:title["\']', html, re.IGNORECASE)
    if match:
        title = match.group(1)
        title = title.replace("&amp;", "&").replace("&#x27;", "'").replace("&quot;", '"').replace("&#39;", "'")

    # Fallback: <title> Tag
    if not title:
        match = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
        if match:
            title = match.group(1).strip()

    logger.info(f"HTML Fallback — Caption ({len(caption)} chars), Title ({len(title)} chars)")
    return caption, title


def _download_audio(url: str, file_id: str) -> str:
    """Lädt Audio aus dem Video herunter."""
    output_path = os.path.join(TMP_DIR, f"{file_id}.%(ext)s")
    final_path = os.path.join(TMP_DIR, f"{file_id}.m4a")

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--extract-audio",
        "--audio-format", "m4a",
        "--audio-quality", "5",
        "--max-filesize", "50m",
        "--output", output_path,
        "--no-warnings",
        "--quiet",
        url,
    ]

    logger.info(f"Downloading audio from: {url}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Download-Timeout: Video ist zu groß oder Server antwortet nicht.")

    if result.returncode != 0:
        error_msg = result.stderr.strip() if result.stderr else "Unbekannter Fehler"
        raise RuntimeError(f"yt-dlp Fehler: {error_msg}")

    if not os.path.exists(final_path):
        for f in os.listdir(TMP_DIR):
            if f.startswith(file_id) and not f.endswith(".mp4"):
                final_path = os.path.join(TMP_DIR, f)
                break
        else:
            raise RuntimeError("Audio-Datei konnte nicht gefunden werden nach Download.")

    logger.info(f"Audio downloaded: {final_path} ({os.path.getsize(final_path)} bytes)")
    return final_path


def _extract_frames(url: str, file_id: str) -> str | None:
    """
    Lädt das Video und extrahiert einige Frames (für OCR).
    Gibt den Ordnerpfad mit den Frames zurück, oder None bei Fehler.
    """
    frames_dir = os.path.join(TMP_DIR, f"{file_id}_frames")
    os.makedirs(frames_dir, exist_ok=True)
    video_path = os.path.join(TMP_DIR, f"{file_id}_video.mp4")

    # Video herunterladen (kleines Format)
    dl_cmd = [
        "yt-dlp",
        "--no-playlist",
        "-f", "worst[ext=mp4]",  # Kleinste Qualität reicht für OCR
        "--max-filesize", "30m",
        "--output", video_path,
        "--no-warnings",
        "--quiet",
        url,
    ]

    try:
        proc = subprocess.run(dl_cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        logger.warning("Video download for frames timed out")
        return None

    if proc.returncode != 0 or not os.path.exists(video_path):
        logger.warning("Could not download video for frame extraction")
        return None

    # Frames extrahieren mit ffmpeg (1 Frame alle 3 Sekunden)
    ffmpeg_cmd = [
        "ffmpeg",
        "-i", video_path,
        "-vf", "fps=1/3",  # 1 Frame alle 3 Sekunden
        "-frames:v", "10",  # Max 10 Frames
        "-q:v", "3",
        os.path.join(frames_dir, "frame_%02d.jpg"),
        "-y",
        "-loglevel", "error",
    ]

    try:
        subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        logger.warning("Frame extraction timed out")

    # Video-Datei aufräumen
    if os.path.exists(video_path):
        os.remove(video_path)

    # Check ob Frames da sind
    frames = [f for f in os.listdir(frames_dir) if f.endswith(".jpg")]
    if not frames:
        os.rmdir(frames_dir)
        return None

    logger.info(f"Extracted {len(frames)} frames to {frames_dir}")
    return frames_dir


def _download_subtitles(url: str, file_id: str) -> str:
    """
    Lädt Untertitel (auto-generiert oder manuell) herunter.
    Instagram & TikTok haben oft Auto-Captions — das ist quasi Sprache als Text!
    """
    sub_base = os.path.join(TMP_DIR, file_id)

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--write-subs",
        "--write-auto-subs",
        "--sub-lang", "de,en,de-orig,en-orig",
        "--convert-subs", "srt",
        "--skip-download",
        "--output", sub_base,
        "--no-warnings",
        "--quiet",
        url,
    ]

    logger.info(f"Downloading subtitles from: {url}")

    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        logger.warning("Subtitle download timed out")
        return ""

    # Suche die .srt-Datei (bevorzugt Deutsch)
    for lang in ["de", "de-orig", "en", "en-orig"]:
        srt_path = f"{sub_base}.{lang}.srt"
        if os.path.exists(srt_path):
            text = _parse_srt(srt_path)
            # Alle .srt-Dateien aufräumen
            _cleanup_subtitle_files(file_id)
            if text:
                logger.info(f"Untertitel gefunden ({lang}, {len(text)} Zeichen): {text[:100]}...")
                return text

    # Fallback: irgendeine .srt-Datei mit unserer file_id
    for f in os.listdir(TMP_DIR):
        if f.startswith(file_id) and f.endswith(".srt"):
            srt_path = os.path.join(TMP_DIR, f)
            text = _parse_srt(srt_path)
            _cleanup_subtitle_files(file_id)
            if text:
                logger.info(f"Untertitel gefunden (fallback, {len(text)} Zeichen)")
                return text

    _cleanup_subtitle_files(file_id)
    logger.info("Keine Untertitel verfügbar")
    return ""


def _parse_srt(srt_path: str) -> str:
    """Parst eine SRT-Untertiteldatei und gibt den reinen Text zurück."""
    try:
        with open(srt_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except OSError:
        return ""

    text_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.isdigit():
            continue
        if "-->" in line:
            continue
        # HTML-Tags entfernen (<i>, </i>, <b> etc.)
        clean = re.sub(r'<[^>]+>', '', line)
        if clean.strip():
            text_lines.append(clean.strip())

    # Doppelte aufeinanderfolgende Zeilen entfernen (typisch für Auto-Subs)
    deduped = []
    for line in text_lines:
        if not deduped or line != deduped[-1]:
            deduped.append(line)

    return " ".join(deduped)


def _cleanup_subtitle_files(file_id: str) -> None:
    """Räumt alle Untertitel-Dateien für eine file_id auf."""
    for f in os.listdir(TMP_DIR):
        if f.startswith(file_id) and (f.endswith(".srt") or f.endswith(".vtt") or f.endswith(".json3")):
            try:
                os.remove(os.path.join(TMP_DIR, f))
            except OSError:
                pass


# Legacy-Kompatibilität
def download_audio(url: str) -> str:
    """Legacy-Funktion — nutze download_video_data() stattdessen."""
    data = download_video_data(url)
    return data["audio_path"]
