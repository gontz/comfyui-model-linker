# Lora Manager 1.2.5 freezes ComfyUI on some HuggingFace model cards

Found 2026-10-16 during a whole-library "Fill empty model info from HuggingFace" run. This
is a bug in Lora Manager, not in Model Linker. It is written down here because Model Linker's
fill is what reaches it most often.

## What happens

Model Linker asks Lora Manager to hydrate a model from its HuggingFace repo
(`POST /api/lm/download-model-source`). Lora Manager runs this call chain:

1. `hydrate_from_source`
2. `PostProcessor._process_enrich_hf_metadata`
3. `convert_readme_to_html`
4. `_md_to_html` (`py/services/agent/skills/enrich_hf_metadata/readme_processor.py`)

It does all of this synchronously, on ComfyUI's asyncio event loop. On some READMEs
`_md_to_html` never returns, so the whole server stops answering until ComfyUI is
restarted. In our run it was stuck for more than three hours on
`mradermacher/Qwen3-VL-8B-Instruct-c_abliterated-v3-GGUF`.

## Cause

This is not slow regex backtracking. It is an infinite loop. The paragraph collector, at
line 1149, stops at any line matching

```python
re.match(r"^(#{1,4}\s|```|---|\*{3,}|\d+\.\s|[-*+]\s|>\s|\|)", l)
```

That pattern is looser than the block readers before it, which accept less:

| Block reader | Accepts only |
|---|---|
| Rule | `^-{3,}\s*$` or `^\*{3,}\s*$` |
| Heading | `#` followed by text |
| Blockquote | a literal `"> "` |
| Table | a following `^\|[\s:-]+\|` separator row |

When a line matches the break pattern but none of the block readers, the paragraph collector
breaks on that first line without consuming it. Then `i` never advances.

Minimal repro: `convert_readme_to_html("***x***")`.

Other shapes that hang:
- `--- text`, `----x`
- `# `, `#### `, `#\t`
- `| a |` with no separator row under it
- `>\tquote`, `> ***x***`
- a table whose separator lacks a leading pipe

## Which model cards

46 READMEs were tested, each with a 10-second timeout:

- **Hang:**
  - all 14 **mradermacher** GGUF cards tested, both static and i1. Their template has the line
    `***For a convenient overview and download list…***`.
  - **DavidAU/Qwen3.8-27B-TURBO-Fable-Cold-Fusion-735-882-…**, on line 4611: `---</p>`.
- **Fine:**
  - bartowski, HauhauCS, unsloth, unslothai, Qwen, huihui-ai
  - other DavidAU repos
  - Comfy-Org (PixelDiT, SeedVR2), Kijai (WanVideo_comfy)

## Bug report for Lora Manager

> **`_md_to_html` loops forever on `***text***`, `--- x`, `# `, unseparated `|` lines and
> freezes the server.** In `readme_processor.py:1149` the paragraph collector breaks on
> `^(#{1,4}\s|```|---|\*{3,}|\d+\.\s|[-*+]\s|>\s|\|)`. That is looser than the block handlers
> above it:
> - a horizontal rule needs `^-{3,}\s*$`;
> - a heading needs content;
> - a blockquote needs `"> "`;
> - a table needs a separator row.
>
> Such a line falls through to the paragraph branch, which breaks on its first line without
> consuming it, so `i` never advances. Repro: `convert_readme_to_html("***x***")`. It hits
> every mradermacher GGUF README, through their `***For a convenient overview…***` line.
>
> **Fix:** after the paragraph loop, add `if not para_lines: para_lines.append(lines[i]); i += 1`.
> That guarantees progress. Tightening the regex alone still leaves `|` lines exposed.
>
> **Also:** `PostProcessor.process` and `_process_enrich_hf_metadata` are `async` but call the
> synchronous converter inline. The `/api/lm/download-model-source` hydration therefore runs
> on ComfyUI's event loop, and one bad README froze the whole server for over three hours.
> Wrap the call in `await asyncio.to_thread(convert_readme_to_html, readme)`.

## A cheap pre-screen

This check agreed with a ground-truth hang detector on all 46 READMEs. It needs the raw
README (`https://huggingface.co/{repo}/raw/main/README.md`), the same file Lora Manager
fetches anyway. Model Linker's `hf_identify.card()` returns the API JSON, which does not
include the README body.

```python
import re
_BREAK = re.compile(r"^(#{1,4}\s|```|---|\*{3,}|\d+\.\s|[-*+]\s|>\s|\|)")
_HANDLED = re.compile(r"^(-{3,}\s*$|\*{3,}\s*$|#{1,4}\s+.+?\s*#*$|> |```|[-*+]\s|\d+\.\s)")
_TABLE_SEP = re.compile(r"^\|[\s:-]+\|")

def lm_readme_would_hang(text: str) -> bool:
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

assert lm_readme_would_hang("***For a convenient overview***")
assert lm_readme_would_hang("text\n---</p>") and lm_readme_would_hang("# ") and lm_readme_would_hang("| a |")
assert not lm_readme_would_hang("***\n# x\n|a|\n|-|\n|b|\n- item\n> q")
```

## Until Lora Manager is fixed

- **Exclude the affected models in Lora Manager.** Model Linker's fill skips models Lora
  Manager marks `exclude`.
- **Or have the fill pre-screen each repo** with the check above before asking Lora Manager to
  hydrate it. This is not built in yet.
- If ComfyUI is already frozen, restart it. The fill stops when Lora Manager stops answering,
  and running it again carries on where it stopped.
