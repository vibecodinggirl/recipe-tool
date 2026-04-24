"""
Video Downloader - Lädt Audio, Caption, Untertitel und Frames aus Instagram Reels & TikTok Videos.
Nutzt yt-dlp als Backend.
"""

import os
import re
import uuid
import json
import subprocess
import logging

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


def download_video_data(url: str) -> dict:
    """
    Lädt Audio + Metadaten (Caption) aus einem Video.
    Gibt ein dict zurück mit:
      - audio_path: Pfad zur Audio-Datei
      - caption: Beschreibungstext des Videos (Caption)
      - title: Titel des Videos
      - frames_dir: Ordner mit extrahierten Frames (für OCR)
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

    # --- 1. Metadaten holen (Caption, Titel) ---
    result["caption"], result["title"] = _fetch_metadata(url)

    # --- 2. Untertitel herunterladen (Auto-Captions = Sprache!) ---
    result["subtitles"] = _download_subtitles(url, file_id)

    # --- 3. Audio herunterladen ---
    result["audio_path"] = _download_audio(url, file_id)

    # --- 4. Video-Frames extrahieren (für Text im Video / OCR) ---
    result["frames_dir"] = _extract_frames(url, file_id)

    return result


def _fetch_metadata(url: str) -> tuple[str, str]:
    """Holt Caption/Beschreibung und Titel des Videos."""
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--skip-download",
        "--dump-json",
        "--no-warnings",
        "--quiet",
        url,
    ]

    logger.info(f"Fetching metadata from: {url}")

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        logger.warning("Metadata fetch timed out")
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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
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
        proc = subprocess.run(dl_cmd, capture_output=True, text=True, timeout=120)
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
        subprocess.run(cmd, capture_output=True, text=True, timeout=60)
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
