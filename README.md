# ComfyUI Model Linker

Open a workflow, and some of its models aren't on your disk: renamed, moved, saved
under another folder, or never downloaded. Model Linker finds every reference the
workflow makes, tells you which ones are missing, suggests the file you most likely
meant, and relinks them in one click. When the file isn't anywhere on your machine, it
finds a download for it.

https://github.com/user-attachments/assets/fedf3645-aa66-49f7-b01d-8c3b5127faf4

![Model Linker](model-linker.png)

## What it does

- **Finds every model reference**: in every node, in nested subgraphs, and in packs that
  store references their own way (rgthree Power Lora Loader, Lora Manager).
- **Suggests the right file, not just a similar name.** `qwen3vl_8b_int8` is matched to
  `qwen3vl_8b_bf16` (same model, other precision), never to `qwen3vl_4b_int8`, which differs
  by only one character but is a different model.
- **Downloads what you don't have**: from HuggingFace, CivitAI or the workflow's own link,
  into the right folder, verified against its hash.
- **Swaps any model** a workflow uses across every node that loads it, in one undo step.
- **Exports a workflow for sharing**, with download links for its models and every token or
  credential stripped out.
- **Writes Lora Manager info** for downloaded models and can fill empty Lora Manager
  entries from HuggingFace.

## Installation

1. Clone this repository into `ComfyUI/custom_nodes/`. (The "Model Linker" entry in
   ComfyUI-Manager's registry installs the upstream original, not this fork.)
2. Install the requirements into ComfyUI's Python: `pip install -r requirements.txt`
   (`requests`; ComfyUI already provides `aiohttp`).
3. Restart ComfyUI.

Optional: with `huggingface_hub` ≥ 1.1 installed, HuggingFace downloads use its faster
multi-connection engine. With [Lora Manager](https://github.com/willmiao/ComfyUI-Lora-Manager)
installed, downloads get Lora Manager info files.

Relinking works without `requests` too. Downloads, background lookups and Export need it.

## Using it

Open Model Linker with <kbd>Alt</kbd>+<kbd>L</kbd>, **View → Model Linker**, the command
palette, or a right-click on the canvas. When a workflow you load has missing models,
Model Linker tells you so once; it can also open by itself (see Settings).

**Relinking.** Each missing model gets a card with its node, the path the workflow asked
for, and suggested matches ranked by confidence. Pick one, or search every model of that
type in the picker. Selections queue on the right; **Apply Selected** writes them into the
workflow as a single undo step. **Auto-Resolve 100% Matches** applies every exact match at
once. A match that sits in a folder the node can't load from is marked **⚠ wrong folder**
and never auto-linked. Save the workflow when you are happy with it.

**Downloading.** When a download is known, the card offers **☁ Download** with its size and
source. **Download all found** fetches every missing model that has one; each file is
downloaded once, however many nodes use it. Downloads run on the server, two at a time, and
carry on if you close the dialog; reopen it and the progress is still there. **🔎 Search
similar** looks online for related files when there is no exact one. Those are scored,
labelled ("same model", "different model") and saved under their own name, never the
workflow's.

**Swap.** *Models found on disk* lists what the workflow already has. **⇄ Swap** replaces one
of them on every node that uses it, including inside subgraphs.

**Locate** jumps to the node, opening the subgraphs it sits in.

**Export…** prepares a workflow for sharing. It finds a download link for each model (or
keeps the one the workflow already records), strips any token, password or signature from
it, and can add a "Model links" note and a small Model Linker node. The node is muted, so
the shared workflow still runs where this extension isn't installed (recipients will see
ComfyUI's usual missing-node notice for it).

## Settings

Model Linker has two places for settings:

- **⚙ Settings in the Model Linker dialog**:
  - HuggingFace token and CivitAI API key, for gated or login-only downloads. They are kept
    on the server and never sent back to the browser; `HF_TOKEN` and `CIVITAI_API_TOKEN`
    work too.
  - **Find downloads in the background**, on by default.
  - **Fill empty model info from HuggingFace**, for Lora Manager users (see below).
- **ComfyUI's own Settings → Model Linker**:
  - **Open Model Linker when a loaded workflow has missing models** (off by default).

### Filling Lora Manager info from HuggingFace

Lora Manager can describe a model from its HuggingFace model card, but only once the model
is linked to a repo. This fills that gap for models whose Lora Manager info is empty:

1. It identifies the repo the file came from **by its SHA-256 only**. A name match alone
   never links.
2. It links the repo.
3. It lets Lora Manager read the card.

Existing trigger words are kept. A model you linked to another site is left alone. The
optional AI enrichment is used only for models that have no trigger words, notes, usage
tips or base model of their own. The run happens on the server and keeps a report, and it
stops by itself if Lora Manager stops answering (see Known issues).

## Privacy: what goes online, and when

- **Background lookups** (on by default) start as soon as a workflow is loaded.
  - For each missing model, the filename (never a path) is sent to HuggingFace and CivitAI.
  - When the workflow records the model's SHA-256, that hash is sent to CivitAI and
    CivArchive.
  - A model that carries its own download link is never looked up; the link is used as is.
  - Turn this off in ⚙ Settings.
- **🔎 Search similar** sends a filename only when you press it.
- **Export** looks up links the same way, unless background lookups are off. In that case it
  sends nothing until you press **Find links**.
- **After a download**, and only if Lora Manager is installed:
  - the file's SHA-256 is sent to CivitAI to fill its Lora Manager info;
  - a preview image is fetched from CivitAI's image CDN.
- **The Lora Manager fill** sends the SHA-256 and filename of models Lora Manager already
  catalogues.
- A URL taken from a workflow is never fetched during lookups. It is only shown to you, and
  downloaded only if you press Download.
- Tokens go only to HuggingFace and CivitAI themselves, as a header, and never appear in
  error messages, logs or exported workflows.

## Known issues

- **Lora Manager (1.2.5) freezes ComfyUI on some HuggingFace model cards.** Its
  README-to-HTML converter (`readme_processor.py`, `_md_to_html`) loops forever on a line
  that its paragraph reader stops at, but that no other block reader accepts. Examples:
  - `***bold italic***` at the start of a line;
  - `--- text`;
  - a `# ` heading with nothing after it;
  - a `| … |` line with no table separator under it.

  It runs on ComfyUI's main loop, so ComfyUI stops answering until it is restarted. All 14
  **mradermacher** GGUF cards we tested have such a line (`***For a convenient overview…***`,
  from their template), and so do some others, such as DavidAU's
  Qwen3.8-27B-TURBO-Fable-Cold-Fusion-735-882. The Lora Manager fill is built to stop as soon
  as Lora Manager stops answering; this is covered by tests but has not yet been tried against
  a real freeze. Until Lora Manager is fixed, exclude those models in Lora Manager; the fill
  skips excluded models. The details, a bug report ready for Lora Manager, and a pre-screen
  are in [docs/lora-manager-readme-hang.md](docs/lora-manager-readme-hang.md).
- **Lora Manager catalogues everything under its model roots.** If a root reaches folders of
  LLM files or HuggingFace caches (through a junction or symlink, for instance), Lora
  Manager lists those too. Exclude them in Lora Manager or narrow its roots.
- Saving a new workflow under a name and then pressing <kbd>Ctrl</kbd>+<kbd>Z</kbd> clears the
  queued selections; pick them again.
- Tested with ComfyUI frontend 1.55.17.

## Changes in this fork

This fork started from kianxyzw's Model Linker and has since been largely rewritten. The
main changes, by area:

**Matching**
- Banded scoring that understands model families. Same model at another precision ranks
  above a different model with a similar name; `8b` vs `4b` and `qwen3` vs `qwen2` are never
  offered as matches. Word order doesn't matter.
- [rapidfuzz](https://github.com/rapidfuzz/RapidFuzz) when available, and a top-N heap: about
  17 times faster on large libraries.
- Category aliases (`clip`/`text_encoders`, `unet`/`diffusion_models`, GGUF loaders …), so a
  model is found whatever name its folder is registered under.
- Linked folders handled by physical file identity, so a model reached through several
  junctions is listed once.
- Saved overrides: a manual pick is remembered, shown first next time, and editable in a
  Manage Overrides dialog.

**Understanding workflows**
- Nested subgraphs, dict-format `widgets_values`, rgthree Power Lora Loader, and Lora
  Manager's own reference format.
- Model categories learned from the installed node classes themselves, so custom nodes it
  has never heard of still work.
- Download links and hashes that newer workflows record in `properties.models`.
- A list of the models the workflow *did* find, not only the missing ones.

**Downloads and lookups** (new in 1.2)
- Background lookups that never slow down analysis, remembered across restarts.
- A server-side download queue. It never overwrites a file, checks the hash before the file
  gets its name, refuses non-model folders, uses https on every redirect, and catches
  duplicates even through linked folders.
- HuggingFace multi-connection engine; disk-space check before every download.
- 🔎 Search similar, scored with the same matcher.

**Lora Manager** (new in 1.2)
- Lora Manager info files written beside downloaded models, in Lora Manager 1.2.5's format,
  without ever overwriting its own files.
- Filling empty entries from HuggingFace, identified by SHA-256, with trigger words kept and
  a circuit breaker.

**Workflow tools** (new in 1.2)
- Swap any model across all its nodes; Locate into nested subgraphs; a notice when a
  workflow loads with missing models; Export with links and credential stripping.

**Interface**
- The frontend was rebuilt as ES modules. Everything is reachable from the menu, Alt+L, the
  command palette or the canvas, with no injected buttons. A resizable dialog with a
  selection queue, a searchable picker with keyboard navigation, a folder location and an
  "open containing folder" button.
- Every value that comes from a workflow file is escaped before it is shown.
- Selections from one workflow are never applied to another, and slow answers for an
  earlier workflow are discarded.

**Under the hood**
- Analysis, resolution and listing run off ComfyUI's main loop.
- A settings store that keeps tokens on the server.
- A test suite of 420+ Python tests plus frontend checks. It builds its own model library
  and never touches the network or your files.

The removed download code from upstream came back in 1.2. It was ported file by file and
redesigned so that lookups no longer slow down analysis and can be turned off.

## Credits

- **[kianxyzw/comfyui-model-linker](https://github.com/kianxyzw/comfyui-model-linker)**:
  - the original Model Linker this fork grew from (MIT, © 2025 kianxyzw);
  - the download sources, source chain, HuggingFace engine, settings store and exporter,
    ported from its v1.2.3;
  - the bundled model index and lists.
- **[Azornes/Comfyui-Model-Resolver](https://github.com/Azornes/Comfyui-Model-Resolver)**
  (MIT, © 2026 Azornes): the ideas behind family-name scoring and category aliases.
  Reimplemented here.
- **[willmiao/ComfyUI-Lora-Manager](https://github.com/willmiao/ComfyUI-Lora-Manager)**
  (GPL-3.0): the info-file format and HTTP routes Model Linker writes to and calls. The format
  is implemented from its documentation; no Lora Manager code is included.
- **[ComfyUI-Manager](https://github.com/Comfy-Org/ComfyUI-Manager)**: its model list, on which
  `metadata/model-list.json` is based.
- **[Comfy-Org](https://huggingface.co/Comfy-Org)** and **[Kijai](https://huggingface.co/Kijai)**:
  their HuggingFace repos make up the bundled hash index.
- **[HuggingFace](https://huggingface.co)**, **[CivitAI](https://civitai.com)** and
  **[CivArchive](https://civarchive.com)**: the services lookups and downloads use.
- **[ComfyUI](https://github.com/comfyanonymous/ComfyUI)** and its frontend team, whose
  extension hooks this is built on.
- Libraries: [requests](https://requests.readthedocs.io),
  [huggingface_hub](https://github.com/huggingface/huggingface_hub),
  [rapidfuzz](https://github.com/rapidfuzz/RapidFuzz), [aiohttp](https://docs.aiohttp.org).
- Version 1.2 was developed with [Claude Code](https://claude.com/claude-code).

Full third-party notices are in [NOTICE.md](NOTICE.md).

## License

MIT, see [LICENSE](LICENSE). Both the upstream and this fork's copyright lines are retained.
