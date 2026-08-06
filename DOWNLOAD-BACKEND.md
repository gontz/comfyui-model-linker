# The download backend

**Status: present, working, and unreachable from the UI.**

This is inherited code from upstream (`kianxyzw/comfyui-model-linker`, MIT). Its server
side is intact and its routes register. Its *interface* was written against the monolithic
`web/linker.js` that this project has since split into `web/modules/`, and did not survive
the merge that brought the two lines of work together — see `CLAUDE.md`, "This fork does
not track upstream".

Nothing here is broken. It simply has no buttons.

## What exists

| File | Size | Purpose |
|---|---|---|
| `core/downloader.py` | ~16 KB | Background downloads, progress tracking, cancellation |
| `core/sources/huggingface.py` | ~9 KB | Search HuggingFace for a filename |
| `core/sources/civitai.py` | ~11 KB | Search CivitAI for a filename |
| `core/sources/model_list.py` | ~7 KB | Search the bundled model list |
| `core/sources/popular.py` | ~4 KB | Look up well-known models by name |
| `metadata/model-list.json` | ~265 KB | Bundled catalogue of known models |
| `metadata/popular-models.json` | ~6 KB | Curated popular models |
| `metadata/model-aliases.json` | ~2 KB | Alternate names for the above |

Plus `requests>=2.28.0` in `requirements.txt`, which exists only for this.

Six routes register, all under `/model_linker/`:

| Route | Purpose |
|---|---|
| `POST /search` | Search for download sources for a model |
| `POST /download` | Start a background download |
| `GET /progress/{download_id}` | Progress for one download |
| `GET /progress` | Progress for all downloads |
| `POST /cancel/{download_id}` | Cancel a download |
| `GET /directories` | Available model directories |

They work. They can be driven with `curl` today.

## The part that is *not* dormant

`POST /model_linker/analyze` is wired into this, and that wiring is live whether or not
anything downloads. For **every missing model without a 100% local match**, the route
tries in order: a URL recorded in the workflow (with a `requests.head` at up to 5s), the
bundled popular list, the bundled model list, then **HuggingFace**, then **CivitAI**.

Two consequences worth knowing about:

- **Analysis is slower.** Measured on this machine, a miss costs roughly 0.19s at
  HuggingFace and 0.30s at CivitAI. The searches run one after another, per missing model.
  A workflow with nine missing models therefore adds on the order of 4–5 seconds to an
  analysis that otherwise completes in about 0.06s warm.
- **Model filenames leave the machine.** They are sent to huggingface.co and civitai.com.
  The names come from the workflow, so this discloses what a workflow references to two
  third parties.

There is **no setting for this**. `download_available` is set by whether
`from .core.downloader import ...` succeeds, nothing else, so the only way to switch it off
today is to make that import fail.

## If you want it off

Deleting `core/downloader.py` is enough — the import guard catches `ImportError`, sets
`download_available = False`, logs a warning, and both the six routes and the analyze
enrichment disappear cleanly. `core/sources/`, `metadata/` and the `requests` dependency
can go with it.

Adding a real switch (an environment variable or a ComfyUI setting consulted alongside the
import) would be tidier and is not currently implemented.

## If you want it back in the UI

The interface was 33 methods on the old dialog class: search results, download buttons,
progress bars, cancel and cancel-all, byte formatting, confidence badges, plus an
integration that injected buttons into ComfyUI's own "Missing Models" popup and an
auto-open-on-missing behaviour. They are recoverable from git:

```
git show <pre-merge-main>:web/linker.js
```

Porting them means re-homing that code into `web/modules/` against the current dialog, and
rendering the `download_source` object that `/analyze` already returns and the frontend
currently ignores. It is a real piece of work, not a patch.
