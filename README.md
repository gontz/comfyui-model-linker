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
- **Auto-Resolve**: One-click resolution for all perfect matches
- **Learned Overrides**: Your manual picks are persisted and reused automatically

## Download support

A download backend is present but has no interface, and `/analyze` searches HuggingFace
and CivitAI for every missing model without a perfect local match — which slows analysis
and sends workflow filenames to those services. See
[DOWNLOAD-BACKEND.md](DOWNLOAD-BACKEND.md).

## License

MIT — see [LICENSE](LICENSE). A fork of
[kianxyzw/comfyui-model-linker](https://github.com/kianxyzw/comfyui-model-linker);
both copyright lines are retained. Third-party attributions are in
[NOTICE.md](NOTICE.md).
