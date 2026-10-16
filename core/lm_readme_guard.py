"""
Keep Lora Manager away from HuggingFace model cards it cannot read.

Lora Manager 1.2.5's README-to-HTML converter (`_md_to_html`) loops forever on
some lines - `***text***` at the start of a line, `--- text`, a `# ` heading
with nothing after it, a `| ... |` line with no table separator under it - and
it runs on ComfyUI's event loop, so one such card freezes the whole server
until it is restarted. Every mradermacher GGUF card has one. See
docs/lora-manager-readme-hang.md.

Lora Manager fetches the card from HuggingFace itself, so the card cannot be
fixed on our side; the only safe move is not to ask it to read that card. Before
Model Linker links or hydrates a repo, `card_is_safe` fetches the same README
Lora Manager would and checks it with `would_hang`.
"""

import logging
import re
from typing import Dict, Optional

from . import settings

logger = logging.getLogger(__name__)

TIMEOUT = 20

# Lora Manager's paragraph reader stops at a line matching _BREAK; a line the
# block readers before it don't take (_HANDLED) is never consumed, so the
# converter makes no progress. Tables are taken only with a separator row.
_BREAK = re.compile(r"^(#{1,4}\s|```|---|\*{3,}|\d+\.\s|[-*+]\s|>\s|\|)")
_HANDLED = re.compile(r"^(-{3,}\s*$|\*{3,}\s*$|#{1,4}\s+.+?\s*#*$|> |```|[-*+]\s|\d+\.\s)")
_TABLE_SEP = re.compile(r"^\|[\s:-]+\|")

# repo -> True (safe) / False (would hang); a fetch that failed is not cached
_verdicts: Dict[str, bool] = {}


def would_hang(text: str) -> bool:
    """Would Lora Manager 1.2.5's _md_to_html loop forever on this README?"""
    if text.startswith("---"):                       # Lora Manager strips frontmatter first
        end = text.find("\n---", 3)
        text = text[end + 4:] if end != -1 else text
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)   # and HTML comments
    lines = text.split("\n")
    in_fence = in_table = False
    for i, line in enumerate(lines):
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if in_table:
            if "|" in line:
                continue
            in_table = False
        if "|" in line and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]):
            in_table = True                          # header; separator and rows are consumed
            continue
        if line.startswith("> "):
            line = line[2:]                          # blockquote content is parsed again
        if _BREAK.match(line) and not _HANDLED.match(line):
            return True
    return False


def _fetch_readme(repo: str) -> Optional[str]:
    """The README Lora Manager would read: `main`, then `master`. '' when there is none."""
    import requests
    for branch in ('main', 'master'):
        url = f'https://huggingface.co/{repo}/raw/{branch}/README.md'
        try:
            response = requests.get(url, headers=settings.auth_headers_for_url(url), timeout=TIMEOUT)
        except Exception as e:
            # Type name only: a malformed token can put the whole header in str(e)
            logger.debug(f"Model Linker: could not fetch {repo}'s model card: {type(e).__name__}")
            return None
        if response.status_code == 200:
            return response.text
        if response.status_code != 404:
            logger.debug(f"Model Linker: HTTP {response.status_code} fetching {repo}'s model card")
            return None
    return ''


def card_is_safe(repo: str) -> Optional[bool]:
    """
    True when Lora Manager can read `repo`'s model card, False when it would
    freeze on it, None when the card could not be fetched (try again later).
    A repo with no README at all is safe: Lora Manager has nothing to convert.
    """
    if repo in _verdicts:
        return _verdicts[repo]
    text = _fetch_readme(repo)
    if text is None:
        return None
    _verdicts[repo] = not would_hang(text)
    return _verdicts[repo]


def clear_cache() -> None:
    """A card can change between runs; lm_backfill clears this at the start of each."""
    _verdicts.clear()
