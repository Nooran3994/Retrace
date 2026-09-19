"""Redaction engine — secrets are masked BEFORE anything is stored.

Design rule: Retrace never stores raw secrets. Every captured line
passes through this module. Patterns are conservative (we over-redact
rather than leak), and the original text is discarded — not reversible.
"""

import re

# Key-name patterns (matched case-insensitively). These are plain
# alternations WITHOUT group parens — the caller wraps each in a group.
SECRET_KEY_PATTERNS = [
    r"api[_-]?key|apikey|secret|token|passwd|password|pwd|auth|credential|private[_-]?key|access[_-]?key",
    r"bearer\s+[a-z0-9._~+/=-]+",
]

# Common secret value shapes (high-entropy tokens, JWT-ish, PATs).
SECRET_VALUE_PATTERNS = [
    r"ghp_[A-Za-z0-9]{20,}",            # GitHub classic PAT
    r"github_pat_[A-Za-z0-9_]{20,}",    # GitHub fine-grained PAT
    r"gho_[A-Za-z0-9]{20,}",            # GitHub OAuth
    r"xox[baprs]-[A-Za-z0-9-]{10,}",    # Slack tokens
    r"AKIA[0-9A-Z]{16}",                # AWS access key id
    r"sk-[A-Za-z0-9]{20,}",             # OpenAI-style keys
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",  # JWT
]

# Whole-line secrets (e.g. `export TOKEN=...`).
ASSIGNMENT_RE = re.compile(
    r"^(\s*(?:export\s+)?[A-Z0-9_]*"
    r"(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|AUTH|CREDENTIAL)"
    r"\s*[=:]\s*).*$"
)

REDACTED = "[REDACTED]"


def _mask_value(match: re.Match) -> str:
    """Replace the matched secret with the redaction marker."""
    return REDACTED


def redact_line(line: str) -> str:
    """Redact secrets in a single line. Returns the masked line."""
    if not line:
        return line

    # Whole-line assignment redaction first (keeps the key name visible).
    line = ASSIGNMENT_RE.sub(lambda m: m.group(1) + REDACTED, line)

    # Mask known secret value shapes.
    for pattern in SECRET_VALUE_PATTERNS:
        line = re.sub(pattern, _mask_value, line)

    # Mask `key=value` inline pairs where key looks secret-ish.
    for pattern in SECRET_KEY_PATTERNS:
        line = re.sub(
            re.compile(r"(" + pattern + r")(\s*[=:]\s*)(\S+)", re.IGNORECASE),
            lambda m: m.group(1) + m.group(2) + REDACTED,
            line,
        )

    # Mask space-separated tokens after auth keywords (e.g. `Bearer <token>`)
    line = re.sub(
        re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~+/=-]{6,})"),
        lambda m: m.group(1) + REDACTED,
        line,
    )

    return line


def redact_lines(lines) -> list:
    """Redact an iterable of lines, returning a list of masked lines."""
    return [redact_line(l) for l in lines]


def is_high_entropy(text: str, threshold: float = 3.5) -> bool:
    """Heuristic: flag likely secret material by character diversity.

    Returns True when the line contains a token whose Shannon entropy
    per byte is above the threshold (random-looking strings). The raw
    text is NOT stored — only the flag is recorded.
    """
    import math

    def shannon(s: str) -> float:
        if not s:
            return 0.0
        freq = {}
        for ch in s:
            freq[ch] = freq.get(ch, 0) + 1
        length = len(s)
        return -sum((c / length) * math.log2(c / length) for c in freq.values())

    for token in re.findall(r"\S{12,}", text):
        if shannon(token) >= threshold:
            return True
    return False