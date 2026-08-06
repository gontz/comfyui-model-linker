"""
Model Category Aliases

ComfyUI reaches one folder of models through several category names. Core
registers `diffusion_models` against both `models/unet` and
`models/diffusion_models`; custom nodes add their own names for the same files
(`unet_gguf`, `model_gguf`, `select_safetensors`); and installations commonly
make `models/clip` a link to `models/text_encoders`, or point `checkpoints`,
`diffusers` and `unet` at one shared directory.

Comparing category names literally therefore says "different" about folders that
hold exactly the same files. Reducing a name to its canonical form first keeps
category-aware ranking and the picker's category scoping working on such setups.

Alias groups modelled on the CATEGORY_MAP in Comfyui-Model-Resolver
(Azornes, MIT), a descendant of this project.
"""

from typing import Optional


# Alias -> canonical category. Names not listed are already canonical, so this
# only needs the genuinely ambiguous ones rather than every ComfyUI category.
CATEGORY_ALIASES = {
    # Diffusion backbones: core registers `unet` and `diffusion_models` for the
    # same folder, and GGUF loaders add more names for the same files.
    'unet': 'diffusion_models',
    'unet_gguf': 'diffusion_models',
    'model_gguf': 'diffusion_models',
    'diffusion_model': 'diffusion_models',
    'diffusion_models_gguf': 'diffusion_models',
    'select_safetensors': 'diffusion_models',
    'select_gguf': 'diffusion_models',

    # Text encoders were called CLIP before multi-encoder models arrived
    'clip': 'text_encoders',
    'clips': 'text_encoders',
    'clip_gguf': 'text_encoders',
    'text_encoder': 'text_encoders',

    # Singular/plural spellings used by various node packs
    'checkpoint': 'checkpoints',
    'lora': 'loras',
    'embedding': 'embeddings',
    'textual_inversion': 'embeddings',
    'hypernetwork': 'hypernetworks',
    'control_net': 'controlnet',
    'style_model': 'style_models',
    'upscale_model': 'upscale_models',
    'upscaler': 'upscale_models',
    'latent_upscale_model': 'latent_upscale_models',
    'model_patch': 'model_patches',
    'optical_flow_model': 'optical_flow',
    'audio_encoder': 'audio_encoders',
    'ip_adapter': 'ipadapter',
    'sam': 'sams',
    'sam_model': 'sams',
}

# Treated as "no category known" rather than as a category named "unknown"
_UNKNOWN_CATEGORIES = {'', 'unknown', 'none', 'undefined', 'any'}


def canonical_category(category: Optional[str]) -> Optional[str]:
    """
    Reduce a category name to the canonical name for its group.

    Returns None when no category is known, so callers can distinguish "no
    category" from a category that simply has no alias.
    """
    if not category:
        return None
    name = str(category).strip().lower()
    if name in _UNKNOWN_CATEGORIES:
        return None
    return CATEGORY_ALIASES.get(name, name)


def categories_match(left: Optional[str], right: Optional[str]) -> bool:
    """Whether two category names refer to the same group of models."""
    left_canonical = canonical_category(left)
    if left_canonical is None:
        return False
    return left_canonical == canonical_category(right)
