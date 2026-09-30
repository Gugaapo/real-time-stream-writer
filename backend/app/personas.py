"""Built-in voices from stream-in-novel, plus one reader-written voice."""
from __future__ import annotations

MAX_CUSTOM_PROMPT = 800
MIN_CUSTOM_PROMPT = 8

PERSONAS: tuple[dict[str, str], ...] = (
    {
        "id": "dumb",
        "name": "Amigo besta",
        "blurb": "Deboche afetuoso, preciso com o que está na tela.",
    },
    {
        "id": "novel",
        "name": "Romance",
        "blurb": "Um capítulo literário em terceira pessoa.",
    },
    {
        "id": "nature",
        "name": "Documentário",
        "blurb": "Um narrador calmo observando o humano no teclado.",
    },
    {
        "id": "noir",
        "name": "Detetive",
        "blurb": "Noir em segunda pessoa. Chuva, neon, más decisões.",
    },
    {
        "id": "custom",
        "name": "Personalizado",
        "blurb": "Você descreve a voz. Ela muda o tom, não os fatos.",
    },
)

STYLE_IDS = frozenset(p["id"] for p in PERSONAS)


def persona_by_id(style: str) -> dict[str, str] | None:
    for persona in PERSONAS:
        if persona["id"] == style:
            return persona
    return None


def clean_custom_prompt(raw: str) -> str:
    """Keep a short style note. Fence markers and control characters are dropped."""
    text = raw or ""
    kept: list[str] = []
    for char in text:
        if char in "\n\t" or (ord(char) >= 32 and char != "\x7f"):
            kept.append(char)
    cleaned = "".join(kept)
    for token in ("<<<READER_STYLE>>>", "<<<END_READER_STYLE>>>", "<<<", ">>>"):
        cleaned = cleaned.replace(token, "")
    lines = [line.strip() for line in cleaned.splitlines()]
    cleaned = "\n".join(line for line in lines if line).strip()
    if len(cleaned) > MAX_CUSTOM_PROMPT:
        cleaned = cleaned[:MAX_CUSTOM_PROMPT].rstrip()
    if len(cleaned) < MIN_CUSTOM_PROMPT:
        raise ValueError("Escreve um pouco mais sobre a voz.")
    return cleaned
