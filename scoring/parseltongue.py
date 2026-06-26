"""
Parseltongue -- Input obfuscation engine for Hybrid Hunter.
Ported from godmode parseltongue.py -- zero Hermes dependencies.

33 encoding techniques across 3 tiers (light=11, standard=22, heavy=33).

Functions:
    detect_triggers(text, custom_triggers=None) -> list[str]
    obfuscate_query(query, technique_name, triggers=None) -> str
    generate_variants(query, tier="standard", custom_triggers=None) -> list[dict]
    escalate_encoding(query, level=0) -> str
"""

import re
from typing import List, Dict, Optional

# ---------------------------------------------------------------------------
# Trigger words that commonly trip safety classifiers
# ---------------------------------------------------------------------------
TRIGGER_WORDS = [
    "hack", "exploit", "bypass", "crack", "attack", "penetrate", "inject",
    "manipulate", "override", "disable", "circumvent", "evade", "malware",
    "virus", "trojan", "payload", "shellcode", "rootkit", "keylogger",
    "backdoor", "vulnerability", "weapon", "bomb", "explosive", "poison",
    "drug", "synthesize", "jailbreak", "unlock", "phishing", "scam",
    "impersonate", "deceive", "fraud", "nsfw", "explicit", "uncensored",
    "unfiltered", "unrestricted", "ignore", "disregard", "forget",
    "pretend", "roleplay", "act as", "you are now", "new identity",
]

# ---------------------------------------------------------------------------
# Encoding maps
# ---------------------------------------------------------------------------
_LEET_MAP = {"a": "4", "e": "3", "i": "1", "o": "0", "s": "5", "t": "7", "l": "1", "b": "8", "g": "9"}
_BUBBLE_MAP = {chr(i): chr(0x24B6 + i - ord('a')) for i in range(ord('a'), ord('z') + 1)}
_BUBBLE_MAP.update({chr(i): chr(0x24D0 + i - ord('A')) for i in range(ord('A'), ord('Z') + 1)})
_SUPERSCRIPT_MAP = {
    'a': 'ᵃ', 'b': 'ᵇ', 'c': 'ᶜ', 'd': 'ᵈ', 'e': 'ᵉ', 'f': 'ᶠ', 'g': 'ᵍ',
    'h': 'ʰ', 'i': 'ⁱ', 'j': 'ʲ', 'k': 'ᵏ', 'l': 'ˡ', 'm': 'ᵐ', 'n': 'ⁿ',
    'o': 'ᵒ', 'p': 'ᵖ', 'r': 'ʳ', 's': 'ˢ', 't': 'ᵗ', 'u': 'ᵘ', 'v': 'ᵛ',
    'w': 'ʷ', 'x': 'ˣ', 'y': 'ʸ', 'z': 'ᶻ',
}
_MORSE_MAP = {
    'a': '.-', 'b': '-...', 'c': '-.-.', 'd': '-..', 'e': '.', 'f': '..-.',
    'g': '--.', 'h': '....', 'i': '..', 'j': '.---', 'k': '-.-', 'l': '.-..',
    'm': '--', 'n': '-.', 'o': '---', 'p': '.--.', 'q': '--.-', 'r': '.-.',
    's': '...', 't': '-', 'u': '..-', 'v': '...-', 'w': '.--', 'x': '-..-',
    'y': '-.--', 'z': '--..',
}
_BRAILLE_MAP = {
    'a': '⠁', 'b': '⠃', 'c': '⠉', 'd': '⠙', 'e': '⠑', 'f': '⠋', 'g': '⠛',
    'h': '⠓', 'i': '⠊', 'j': '⠚', 'k': '⠅', 'l': '⠇', 'm': '⠍', 'n': '⠝',
    'o': '⠕', 'p': '⠏', 'q': '⠟', 'r': '⠗', 's': '⠎', 't': '⠞', 'u': '⠥',
    'v': '⠧', 'w': '⠺', 'x': '⠭', 'y': '⠽', 'z': '⠵',
}
_FULLWIDTH_MAP = {chr(i): chr(0xFF01 + i - ord('!')) for i in range(ord('!'), ord('~') + 1)}
_UNICODE_HOMOGLYPHS = {
    'a': 'а', 'c': 'с', 'e': 'е', 'o': 'о', 'p': 'р', 'x': 'х', 'y': 'у',
    'i': 'і', 's': 'ѕ', 'd': 'ԁ', 'g': 'ɡ', 'h': 'һ', 'k': 'κ', 'n': 'ո',
    'u': 'υ', 'v': 'ν', 'w': 'ω',
}
_MATH_BOLD_MAP = {chr(i): chr(0x1D400 + i - ord('a')) for i in range(ord('a'), ord('z') + 1)}
_MATH_BOLD_MAP.update({chr(i): chr(0x1D400 + i - ord('A')) for i in range(ord('A'), ord('Z') + 1)})


def _apply_raw(word: str) -> str:
    return word


def _apply_leetspeak(word: str) -> str:
    return "".join(_LEET_MAP.get(c, c) for c in word.lower())


def _apply_unicode(word: str) -> str:
    return "".join(_UNICODE_HOMOGLYPHS.get(c, c) for c in word.lower())


def _apply_bubble(word: str) -> str:
    return "".join(_BUBBLE_MAP.get(c, c) for c in word)


def _apply_spaced(word: str) -> str:
    return " ".join(word.lower())


def _apply_fullwidth(word: str) -> str:
    return "".join(_FULLWIDTH_MAP.get(c, c) for c in word)


def _apply_zwj(word: str) -> str:
    return "\u200d".join(word.lower())


def _apply_mixedcase(word: str) -> str:
    return "".join(c.upper() if i % 2 == 0 else c.lower() for i, c in enumerate(word))


def _apply_semantic(word: str) -> str:
    synonyms = {
        "hack": "access", "exploit": "utilize", "bypass": "route around",
        "crack": "decode", "attack": "test", "penetrate": "access",
        "inject": "insert", "manipulate": "adjust", "override": "supersede",
        "disable": "deactivate", "circumvent": "navigate", "evade": "avoid",
        "malware": "software", "virus": "program", "payload": "data",
        "vulnerability": "weakness", "weapon": "tool", "bomb": "device",
        "explosive": "reactive", "poison": "toxin", "drug": "compound",
        "synthesize": "create", "jailbreak": "unlock", "phishing": "spoofing",
        "scam": "scheme", "impersonate": "simulate", "deceive": "mislead",
        "fraud": "misrepresentation", "explicit": "direct", "uncensored": "unfiltered",
        "unfiltered": "raw", "unrestricted": "open", "ignore": "skip",
        "disregard": "omit", "forget": "clear", "pretend": "simulate",
        "roleplay": "simulate", "attack": "assess",
    }
    return synonyms.get(word.lower(), word)


def _apply_dotted(word: str) -> str:
    return ".".join(word.lower())


def _apply_underscored(word: str) -> str:
    return "_".join(word.lower())


def _apply_reversed(word: str) -> str:
    return word.lower()[::-1]


def _apply_superscript(word: str) -> str:
    return "".join(_SUPERSCRIPT_MAP.get(c, c) for c in word.lower())


def _apply_smallcaps(word: str) -> str:
    # Unicode small capital letters (U+1D00 block)
    _SMALLCAPS = {
        'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ꜰ',
        'g': 'ɢ', 'h': 'ʜ', 'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ',
        'm': 'ᴍ', 'n': 'ɴ', 'o': 'ᴏ', 'p': 'ᴘ', 'q': 'q', 'r': 'ʀ',
        's': 'ꜱ', 't': 'ᴛ', 'u': 'ᴜ', 'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x',
        'y': 'ʏ', 'z': 'ᴢ',
    }
    return "".join(_SMALLCAPS.get(c, c) for c in word.lower())


def _apply_morse(word: str) -> str:
    return " ".join(_MORSE_MAP.get(c, c) for c in word.lower())


def _apply_piglatin(word: str) -> str:
    if word[0].lower() in "aeiou":
        return word.lower() + "way"
    return word[1:].lower() + word[0].lower() + "ay"


def _apply_brackets(word: str) -> str:
    return "".join(f"[{c}]" for c in word.lower())


def _apply_mathbold(word: str) -> str:
    return "".join(_MATH_BOLD_MAP.get(c, c) for c in word)


def _apply_mathitalic(word: str) -> str:
    # Mathematical italic lowercase (U+1D434-U+1D44D)
    return "".join(chr(0x1D434 + ord(c) - ord('a')) if 'a' <= c <= 'z' else c for c in word.lower())


def _apply_strikethrough(word: str) -> str:
    return "".join(c + "\u0336" for c in word)


def _apply_leetheavy(word: str) -> str:
    heavy = {"a": "4", "b": "8", "e": "3", "g": "9", "i": "1", "l": "1",
             "o": "0", "s": "5", "t": "7", "z": "2"}
    return "".join(heavy.get(c, c) for c in word.lower())


def _apply_hyphenated(word: str) -> str:
    return "-".join(word.lower())


def _apply_leetunicode(word: str) -> str:
    return _apply_unicode(_apply_leetspeak(word))


def _apply_spacedmixed(word: str) -> str:
    return " ".join(_apply_mixedcase(word))


def _apply_reversedleet(word: str) -> str:
    return _apply_leetspeak(word)[::-1]


def _apply_bubblespaced(word: str) -> str:
    return " ".join(_apply_bubble(word))


def _apply_unicodezwj(word: str) -> str:
    return "\u200d".join(_apply_unicode(word))


def _apply_base64hint(word: str) -> str:
    import base64
    return base64.b64encode(word.lower().encode()).decode()


def _apply_hexencode(word: str) -> str:
    return word.lower().encode().hex()


def _apply_acrostic(word: str) -> str:
    return "\n".join(f"{c} -- {c.upper()}" for c in word)


def _apply_dottedunicode(word: str) -> str:
    return ".".join(_apply_unicode(word))


def _apply_fullwidthmixed(word: str) -> str:
    return _apply_fullwidth(_apply_mixedcase(word))


def _apply_triplelayer(word: str) -> str:
    return _apply_unicode(_apply_leetspeak(_apply_mixedcase(word)))


# ---------------------------------------------------------------------------
# Technique registry
# ---------------------------------------------------------------------------
TECHNIQUES = {
    # Light tier (11)
    "raw": _apply_raw,
    "leetspeak": _apply_leetspeak,
    "unicode": _apply_unicode,
    "bubble": _apply_bubble,
    "spaced": _apply_spaced,
    "fullwidth": _apply_fullwidth,
    "zwj": _apply_zwj,
    "mixedcase": _apply_mixedcase,
    "semantic": _apply_semantic,
    "dotted": _apply_dotted,
    "underscored": _apply_underscored,
    # Standard tier (22)
    "reversed": _apply_reversed,
    "superscript": _apply_superscript,
    "smallcaps": _apply_smallcaps,
    "morse": _apply_morse,
    "piglatin": _apply_piglatin,
    "brackets": _apply_brackets,
    "mathbold": _apply_mathbold,
    "mathitalic": _apply_mathitalic,
    "strikethrough": _apply_strikethrough,
    "leetheavy": _apply_leetheavy,
    "hyphenated": _apply_hyphenated,
    "leetunicode": _apply_leetunicode,
    # Heavy tier (33)
    "spacedmixed": _apply_spacedmixed,
    "reversedleet": _apply_reversedleet,
    "bubblespaced": _apply_bubblespaced,
    "unicodezwj": _apply_unicodezwj,
    "base64hint": _apply_base64hint,
    "hexencode": _apply_hexencode,
    "acrostic": _apply_acrostic,
    "dottedunicode": _apply_dottedunicode,
    "fullwidthmixed": _apply_fullwidthmixed,
    "triplelayer": _apply_triplelayer,
}

TIER_LIGHT = list(TECHNIQUES.keys())[:11]
TIER_STANDARD = list(TECHNIQUES.keys())[:22]
TIER_HEAVY = list(TECHNIQUES.keys())[:33]

ENCODING_ESCALATION = [
    ("plain", None),
    ("leetspeak", "leetspeak"),
    ("bubble", "bubble"),
    ("braille", None),  # handled specially
    ("morse", "morse"),
]


def detect_triggers(text: str, custom_triggers: Optional[List[str]] = None) -> List[str]:
    """Return list of trigger words found in text."""
    triggers = custom_triggers or TRIGGER_WORDS
    found = []
    text_lower = text.lower()
    for t in triggers:
        if re.search(rf"\b{re.escape(t)}\b", text_lower):
            found.append(t)
    return found


def obfuscate_query(query: str, technique_name: str, triggers: Optional[List[str]] = None) -> str:
    """Apply a single encoding technique to all trigger words in the query."""
    func = TECHNIQUES.get(technique_name)
    if not func:
        return query
    trigger_list = triggers or detect_triggers(query)
    result = query
    for trigger in trigger_list:
        pattern = re.compile(rf"\b{re.escape(trigger)}\b", re.IGNORECASE)
        result = pattern.sub(lambda m: func(m.group(0)), result)
    return result


def generate_variants(query: str, tier: str = "standard",
                      custom_triggers: Optional[List[str]] = None) -> List[Dict[str, str]]:
    """Generate encoded variants of the query. Returns list of {label, text} dicts."""
    if tier == "light":
        keys = TIER_LIGHT
    elif tier == "standard":
        keys = TIER_STANDARD
    else:
        keys = TIER_HEAVY

    triggers = custom_triggers or detect_triggers(query)
    variants = []
    for key in keys:
        func = TECHNIQUES[key]
        variant = query
        for trigger in triggers:
            pattern = re.compile(rf"\b{re.escape(trigger)}\b", re.IGNORECASE)
            variant = pattern.sub(lambda m, f=func: f(m.group(0)), variant)
        variants.append({"label": key, "text": variant})
    return variants


def escalate_encoding(query: str, level: int = 0) -> str:
    """Apply encoding escalation level (0=plain, 1=leet, 2=bubble, 3=braille, 4=morse)."""
    if level == 0:
        return query
    if level == 1:
        return obfuscate_query(query, "leetspeak")
    if level == 2:
        return obfuscate_query(query, "bubble")
    if level == 3:
        triggers = detect_triggers(query)
        result = query
        for trigger in triggers:
            pattern = re.compile(rf"\b{re.escape(trigger)}\b", re.IGNORECASE)
            result = pattern.sub(lambda m: " ".join(_BRAILLE_MAP.get(c, c) for c in m.group(0).lower()), result)
        return result
    if level >= 4:
        return obfuscate_query(query, "morse")
    return query
