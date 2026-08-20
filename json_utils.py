"""Robuste Hilfsfunktionen für JSON-Antworten von Sprachmodellen."""

import json


def parse_json_object(raw: str) -> dict:
    """Extrahiert genau ein JSON-Objekt aus Text oder einem Markdown-Codeblock."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("Die Modellantwort ist leer.")

    text = raw.strip()
    if "```" in text:
        blocks = text.split("```")
        fenced_blocks = [block.removeprefix("json").strip() for block in blocks[1::2]]
        text = next((block for block in fenced_blocks if "{" in block), text)

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end < start:
        raise ValueError("Die Modellantwort enthält kein JSON-Objekt.")

    try:
        value = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"Die Modellantwort enthält ungültiges JSON: {exc.msg}.") from exc

    if not isinstance(value, dict):
        raise ValueError("Die Modellantwort muss ein JSON-Objekt enthalten.")
    return value
