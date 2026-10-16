# ComfyUI Model Linker Extension

A ComfyUI extension that helps users relink missing models in workflows using fuzzy matching.

https://github.com/user-attachments/assets/fedf3645-aa66-49f7-b01d-8c3b5127faf4

![Model Linker Interface](model-linker.png)


## Features

- Scans all nodes in workflows to find missing models
- Uses fuzzy matching to suggest similar model files
- Updates workflow JSON in UI/memory (user saves themselves)
- Supports all node types
- Optional auto-resolve for 100% confidence matches
- Remembers manual selections: when you pick a replacement for a non-100% match, your choice is saved and shown as a perfect match next time
- Dropdown with all models and search box to quickly pick the correct file

## Installation

1. Clone or download this repository
2. Place it in your ComfyUI custom_nodes/ directory
3. Restart ComfyUI

## Usage

1. Open a workflow with missing models
2. Open Model Linker in whichever way suits you:
   - **View → Model Linker** in the menu
   - <kbd>Alt</kbd>+<kbd>L</kbd>
   - the command palette
   - right-click the canvas
3. Review missing models and their suggested matches
4. Select replacements for individual models (via suggestion buttons or full model dropdown with search)
5. Click "Apply Selected" to relink queued selections, or use "Auto-Resolve 100% Matches" for perfect matches
6. Save your workflow when ready


## Features

- **Subgraph Support**: Automatically detects and handles missing models inside subgraphs
- **Smart Matching**: Shows 100% confidence matches when available, otherwise shows best matches (≥70% confidence)
- **Fuzzy Matching**: Uses intelligent similarity scoring to find model files even with different naming
- **Category-Aware Suggestions**: A plausible match from the node's own model category is
  offered ahead of a better-scoring file from elsewhere, since a path only resolves against
  its own category's folder
- **Handles Linked Model Folders**: Where several category folders are symlinks or junctions
  to one shared directory, each model is still listed once rather than once per alias
- **Download Links**: When a workflow records where a model came from, the original download
  is offered — useful when nothing on disk resembles it
- **Background Lookups**: For a missing model with no link of its own, Model Linker checks
  HuggingFace and CivitAI for a matching download in the background after the analysis
  already answered, so missing-model results never wait on the network. Results are shown
  as they arrive and persist for next time. Turn this off any time in ⚙ Settings
  (`auto_lookup`)
- **Downloads**: Start a found or looked-up download straight from the dialog — two at a
  time, continuing on the server even if you close the dialog or the tab
- **Lora Manager Info**: A downloaded model gets a Lora Manager-format sidecar (trigger
  words, base model, preview image) when CivitAI has that data, without overwriting
  anything Lora Manager already wrote
- **🔎 Search Similar**: User-initiated — nothing is searched automatically. A chosen file
  downloads and keeps its own filename, never the missing model's name, and a file whose
  name already matches something on screen is refused (shared/junctioned model folders
  would otherwise catalogue it twice)
- **Fill Lora Manager Info from HuggingFace**: From ⚙ Settings, backfill empty Lora Manager
  entries (trigger words, base model, description) for models Lora Manager already
  catalogues, identifying the HuggingFace repo by the file's SHA-256. No forced library
  scans. Optional AI enrichment is limited to models with no trigger words, notes, usage
  tips or base model already set
- **Swap**: Replace a model used by several nodes — including across subgraphs — in one
  action, without saving an override (so it won't silently apply to other workflows)
- **Locate in Subgraphs**: Jump straight to a reference that lives inside a nested subgraph
- **Export**: Share a workflow with its model links intact. Download URLs are stripped of
  tokens and credentials before they're written; a muted **Model Linker** node is added so
  the shared workflow still runs with this extension not installed
- **Load-Time Notice**: Opening a workflow with missing models can open Model Linker
  automatically (⚙ Settings → "Open on missing models"), once per workflow
- **Auto-Resolve**: One-click resolution for all perfect matches
- **Learned Overrides**: Your manual picks are persisted and reused automatically

## API tokens

⚙ Settings accepts a HuggingFace token and a CivitAI API token, used for downloads and
lookups against gated or rate-limited models. Each can also come from an environment
variable instead (`HF_TOKEN`, `CIVITAI_API_TOKEN`) — the environment variable is used when
no token is saved in Settings. Tokens are never echoed back in error messages.

## Privacy

- **Background lookups** send a missing model's filename (never a path) to HuggingFace and
  CivitAI to look for a matching download. Turn this off in ⚙ Settings (`auto_lookup`); it
  never runs for a model that already carries its own download link.
- **🔎 Search similar** sends a filename online only when you press it for that specific
  model.
- **Export** sends nothing by default. Pressing "Find links" in the Export dialog looks up
  download links for models that don't have one yet, same as a background lookup.
- **The Lora Manager HuggingFace fill** (⚙ Settings) sends the SHA-256 and filename of
  models Lora Manager already catalogues, to identify the matching HuggingFace repo.
- Nothing contacts a workflow's own recorded URL during any of the above — that link is
  only ever shown to you, never fetched.

## Downloads

Model Linker makes network calls only for the features above, and only when they run:
background lookups (unless turned off), downloads you start, 🔎 Search similar, Export's
"Find links", and the Lora Manager HuggingFace fill. See **Privacy** above for exactly what
each one sends.

## License

MIT — see [LICENSE](LICENSE). A fork of
[kianxyzw/comfyui-model-linker](https://github.com/kianxyzw/comfyui-model-linker);
both copyright lines are retained. Third-party attributions are in
[NOTICE.md](NOTICE.md).
