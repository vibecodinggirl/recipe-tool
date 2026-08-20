"""
OCR - Extrahiert Text aus Video-Frames.
Nutzt pytesseract (Tesseract OCR) für Texteinblendungen in Videos.
Falls Tesseract nicht installiert ist, wird OCR übersprungen.
"""

from __future__ import annotations

import os
import logging

logger = logging.getLogger(__name__)


def extract_text_from_frames(frames_dir: str | None) -> str:
    """
    Liest Text aus allen Frames in einem Ordner via OCR.
    Gibt den kombinierten Text zurück, oder "" bei Fehler/nicht verfügbar.
    """
    if not frames_dir or not os.path.isdir(frames_dir):
        return ""

    try:
        from PIL import Image
        import pytesseract
    except ImportError:
        logger.info("pytesseract/Pillow nicht installiert — OCR übersprungen")
        return ""

    frames = sorted(
        f for f in os.listdir(frames_dir) if f.endswith((".jpg", ".png"))
    )

    if not frames:
        return ""

    all_texts = []
    seen = set()

    for frame_file in frames:
        frame_path = os.path.join(frames_dir, frame_file)
        try:
            img = Image.open(frame_path)
            text = pytesseract.image_to_string(img, lang="deu+eng")
            text = text.strip()

            # Deduplizieren (gleicher Text in mehreren Frames)
            if text and text not in seen and len(text) > 5:
                seen.add(text)
                all_texts.append(text)
        except Exception as e:
            logger.warning(f"OCR Fehler bei {frame_file}: {e}")
            continue

    combined = "\n".join(all_texts)
    logger.info(f"OCR Text ({len(combined)} Zeichen) aus {len(all_texts)} Frames")
    return combined


def cleanup_frames(frames_dir: str | None) -> None:
    """Löscht den Frames-Ordner."""
    if not frames_dir or not os.path.isdir(frames_dir):
        return
    for f in os.listdir(frames_dir):
        try:
            os.remove(os.path.join(frames_dir, f))
        except OSError:
            pass
    try:
        os.rmdir(frames_dir)
    except OSError:
        pass
