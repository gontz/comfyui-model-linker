"""
@author: Model Linker Team
@title: ComfyUI Model Linker
@nickname: Model Linker
@version: 1.0.0
@description: Extension for relinking missing models in ComfyUI workflows using fuzzy matching
"""

import logging
import json

# Web directory for JavaScript interface
WEB_DIRECTORY = "./web"

# Empty NODE_CLASS_MAPPINGS - we don't provide custom nodes, only web extension
# This prevents ComfyUI from showing "IMPORT FAILED" message
NODE_CLASS_MAPPINGS = {}

__all__ = ["WEB_DIRECTORY"]


class ModelLinkerExtension:
    """Main extension class for Model Linker."""
    
    def __init__(self):
        self.routes_setup = False
        self.logger = logging.getLogger(__name__)
    
    def initialize(self):
        """Initialize the extension and set up API routes."""
        try:
            self.setup_routes()
            self.logger.info("Model Linker: Extension initialized successfully")
        except Exception as e:
            self.logger.error(f"Model Linker: Extension initialization failed: {e}", exc_info=True)
    
    def setup_routes(self):
        """Register API routes for the Model Linker extension."""
        if self.routes_setup:
            return  # Already set up
        
        try:
            from aiohttp import web
            
            # Try to get routes from PromptServer
            try:
                from server import PromptServer
                if not hasattr(PromptServer, 'instance') or PromptServer.instance is None:
                    self.logger.debug("Model Linker: PromptServer not available yet")
                    return False
                
                routes = PromptServer.instance.routes
            except (ImportError, AttributeError) as e:
                self.logger.debug(f"Model Linker: Could not access PromptServer: {e}")
                return False
            
            # Import linker modules - use relative imports which should work for packages
            try:
                from .core.linker import analyze_and_find_matches, apply_resolution, list_available_models
                from .core.overrides import (
                    record_overrides,
                    load_overrides,
                    get_overrides_path,
                    delete_override,
                    clear_overrides,
                    replace_overrides,
                )
            except ImportError as e:
                self.logger.error(f"Model Linker: Could not import core modules: {e}")
                return False
            
            # Import download modules
            try:
                from .core.downloader import (
                    start_background_download, get_progress, get_all_progress,
                    cancel_download, get_download_directory
                )
                from .core.sources.popular import get_popular_model_url, search_popular_models
                from .core.sources.model_list import search_model_list, search_model_list_multiple
                from .core.sources.huggingface import search_huggingface_for_file
                from .core.sources.civitai import search_civitai_for_file
                download_available = True
            except ImportError as e:
                self.logger.warning(f"Model Linker: Download features not available: {e}")
                download_available = False

            @routes.post("/model_linker/analyze")
            async def analyze_workflow(request):
                """Analyze workflow and return missing models with matches."""
                try:
                    data = await request.json()
                    workflow_json = data.get('workflow')
                    
                    if not workflow_json:
                        return web.json_response(
                            {'error': 'Workflow JSON is required'},
                            status=400
                        )
                    
                    # Analyze and find matches
                    result = analyze_and_find_matches(workflow_json)
                    
                    # If download available, auto-search for download sources when no 100% local match
                    if download_available:
                        for missing in result.get('missing_models', []):
                            # Check if there's a 100% local match
                            matches = missing.get('matches', [])
                            has_perfect_match = any(m.get('confidence', 0) == 100 for m in matches)
                            
                            if not has_perfect_match:
                                filename = missing.get('original_path', '').split('/')[-1].split('\\')[-1]
                                
                                # 0. Check workflow URL first (highest priority - directly from workflow)
                                workflow_url = missing.get('workflow_url', '')
                                if workflow_url:
                                    # Determine source from URL
                                    if 'huggingface.co' in workflow_url:
                                        source = 'huggingface'
                                    elif 'civitai.com' in workflow_url:
                                        source = 'civitai'
                                    else:
                                        source = 'workflow'
                                    
                                    # Try to get file size with HEAD request (non-blocking, timeout quickly)
                                    file_size = None
                                    try:
                                        import requests
                                        head_response = requests.head(workflow_url, allow_redirects=True, timeout=5)
                                        if head_response.status_code == 200:
                                            file_size = int(head_response.headers.get('content-length', 0))
                                    except Exception:
                                        pass  # Size unknown is fine
                                    
                                    missing['download_source'] = {
                                        'source': source,
                                        'url': workflow_url,
                                        'filename': filename,
                                        'directory': missing.get('workflow_directory', '') or missing.get('category', 'checkpoints'),
                                        'match_type': 'exact',
                                        'url_source': 'workflow',
                                        'size': file_size
                                    }
                                    continue
                                
                                # 1. Check popular models (always exact match)
                                popular_info = get_popular_model_url(filename)
                                if popular_info:
                                    missing['download_source'] = {
                                        'source': 'popular',
                                        'url': popular_info.get('url'),
                                        'filename': filename,
                                        'type': popular_info.get('type'),
                                        'directory': popular_info.get('directory'),
                                        'match_type': 'exact'
                                    }
                                    continue
                                
                                # 2. Check model list (ComfyUI Manager database)
                                # Use exact_only=True to avoid confusing fuzzy matches for downloads
                                model_list_result = search_model_list(filename, exact_only=True)
                                if model_list_result:
                                    missing['download_source'] = {
                                        'source': 'model_list',
                                        'url': model_list_result.get('url'),
                                        'filename': model_list_result.get('filename'),
                                        'name': model_list_result.get('name'),
                                        'type': model_list_result.get('type'),
                                        'directory': model_list_result.get('directory'),
                                        'size': model_list_result.get('size'),
                                        'match_type': model_list_result.get('match_type'),
                                        'confidence': model_list_result.get('confidence')
                                    }
                                    continue
                                
                                # 3. Search HuggingFace (exact_only=True for downloads)
                                hf_result = search_huggingface_for_file(filename, exact_only=True)
                                if hf_result:
                                    missing['download_source'] = {
                                        'source': 'huggingface',
                                        'url': hf_result.get('url'),
                                        'filename': hf_result.get('filename'),
                                        'name': hf_result.get('repo_id', ''),
                                        'size': hf_result.get('size', ''),
                                        'match_type': hf_result.get('match_type', 'exact')
                                    }
                                    continue
                                
                                # 4. Search CivitAI (exact_only=True for downloads)
                                civitai_result = search_civitai_for_file(filename, exact_only=True)
                                if civitai_result:
                                    missing['download_source'] = {
                                        'source': 'civitai',
                                        'url': civitai_result.get('download_url'),  # CivitAI uses download_url
                                        'filename': civitai_result.get('filename'),
                                        'name': civitai_result.get('name', ''),
                                        'size': civitai_result.get('size', ''),
                                        'match_type': civitai_result.get('match_type', 'exact')
                                    }
                    

                    return web.json_response(result)
                except Exception as e:
                    self.logger.error(f"Model Linker analyze error: {e}", exc_info=True)
                    return web.json_response(
                        {'error': str(e)},
                        status=500
                    )
            
            @routes.post("/model_linker/resolve")
            async def resolve_models(request):
                """Apply model resolution and return updated workflow."""
                try:
                    data = await request.json()
                    workflow_json = data.get('workflow')
                    resolutions = data.get('resolutions', [])
                    
                    if not workflow_json:
                        return web.json_response(
                            {'error': 'Workflow JSON is required'},
                            status=400
                        )
                    
                    if not resolutions:
                        return web.json_response(
                            {'error': 'Resolutions array is required'},
                            status=400
                        )
                    
                    # Make a deep copy of workflow to recover original widget values if needed
                    try:
                        workflow_before = json.loads(json.dumps(workflow_json))
                    except Exception:
                        workflow_before = None

                    # Apply resolutions
                    updated_workflow = apply_resolution(workflow_json, resolutions)

                    # Persist user choices as overrides (so next time we know the correct match)
                    try:
                        # helper to fetch the pre-update widget value
                        def _get_widget_value(widgets_values, widget_index):
                            """Safely get a value from widgets_values (array or dict)."""
                            if isinstance(widgets_values, dict):
                                return widgets_values.get(widget_index)
                            elif isinstance(widgets_values, (list, tuple)):
                                if isinstance(widget_index, int) and 0 <= widget_index < len(widgets_values):
                                    return widgets_values[widget_index]
                            return None

                        def _get_original_value(wf, node_id, widget_index, subgraph_id=None, is_top_level=None, nested_key=None):
                            try:
                                if not wf:
                                    return None
                                value = None
                                # Decide where to look for node
                                if is_top_level is False or (is_top_level is None and subgraph_id):
                                    # Search subgraph definitions
                                    defs = (wf.get('definitions') or {}).get('subgraphs') or []
                                    for sg in defs:
                                        if sg.get('id') == subgraph_id:
                                            for n in sg.get('nodes') or []:
                                                if n.get('id') == node_id:
                                                    value = _get_widget_value(n.get('widgets_values'), widget_index)
                                                    break
                                            break
                                if value is None:
                                    # Fallback/top-level
                                    for n in wf.get('nodes') or []:
                                        if n.get('id') == node_id:
                                            value = _get_widget_value(n.get('widgets_values'), widget_index)
                                            break
                                # Extract nested key for dict-type widgets (e.g. Power Lora Loader)
                                if nested_key and isinstance(value, dict):
                                    return value.get(nested_key)
                                return value
                            except Exception:
                                return None

                        selections = []
                        for res in resolutions:
                            # Expect original_path from client; otherwise derive from pre-update workflow
                            original_path = res.get('original_path')
                            if not original_path:
                                original_path = _get_original_value(
                                    workflow_before,
                                    res.get('node_id'),
                                    res.get('widget_index', 0),
                                    res.get('subgraph_id'),
                                    res.get('is_top_level'),
                                    res.get('nested_key')
                                )
                            resolved_model = res.get('resolved_model')
                            resolved_path = res.get('resolved_path')
                            if original_path and (resolved_model or resolved_path):
                                selections.append({
                                    'original_path': original_path,
                                    'category': res.get('category'),
                                    'resolved': resolved_model,
                                    'resolved_path': resolved_path,
                                })

                        # One read and one write for the whole batch
                        record_overrides(selections)
                    except Exception as e:
                        # Do not fail the request if persisting overrides fails
                        self.logger.warning(f"Model Linker: Failed to record overrides: {e}")
                    
                    return web.json_response({
                        'workflow': updated_workflow,
                        'success': True
                    })
                except Exception as e:
                    self.logger.error(f"Model Linker resolve error: {e}", exc_info=True)
                    return web.json_response(
                        {'error': str(e), 'success': False},
                        status=500
                    )
            
            @routes.get("/model_linker/models")
            async def get_models(request):
                """Get list of all available models (for debugging/UI display)."""
                try:
                    models = list_available_models()
                    return web.json_response(models)
                except Exception as e:
                    self.logger.error(f"Model Linker get_models error: {e}", exc_info=True)
                    return web.json_response(
                        {'error': str(e)},
                        status=500
                    )

            @routes.get("/model_linker/overrides")
            async def get_overrides(request):
                """Return current overrides and the file path used for persistence."""
                try:
                    doc = load_overrides()
                    path = get_overrides_path()
                    exists = False
                    try:
                        import os
                        exists = os.path.exists(path)
                    except Exception:
                        pass
                    return web.json_response({
                        'path': path,
                        'exists': exists,
                        'overrides': doc,
                    })
                except Exception as e:
                    self.logger.error(f"Model Linker get_overrides error: {e}", exc_info=True)
                    return web.json_response({'error': str(e)}, status=500)

            @routes.post("/model_linker/overrides/delete")
            async def delete_override_api(request):
                try:
                    data = await request.json()
                    key = (data or {}).get('key')
                    if not key:
                        return web.json_response({'success': False, 'error': 'key is required'}, status=400)
                    removed = delete_override(key)
                    return web.json_response({'success': removed})
                except Exception as e:
                    self.logger.error(f"Model Linker delete_override error: {e}", exc_info=True)
                    return web.json_response({'success': False, 'error': str(e)}, status=500)

            @routes.post("/model_linker/overrides/clear")
            async def clear_overrides_api(request):
                try:
                    clear_overrides()
                    return web.json_response({'success': True})
                except Exception as e:
                    self.logger.error(f"Model Linker clear_overrides error: {e}", exc_info=True)
                    return web.json_response({'success': False, 'error': str(e)}, status=500)

            @routes.post("/model_linker/overrides/replace")
            async def replace_overrides_api(request):
                try:
                    data = await request.json()
                    overrides_doc = data.get('overrides')
                    if overrides_doc is None:
                        return web.json_response({'success': False, 'error': 'overrides payload required'}, status=400)
                    ok = replace_overrides(overrides_doc)
                    return web.json_response({'success': ok})
                except Exception as e:
                    self.logger.error(f"Model Linker replace_overrides error: {e}", exc_info=True)
                    return web.json_response({'success': False, 'error': str(e)}, status=500)

            @routes.post("/model_linker/reveal")
            async def reveal_model(request):
                """
                Open the file manager on the machine running ComfyUI, with the
                named model selected.

                Restricted to requests from the same machine: a file manager
                window on the server is of no use to a remote user, and is an
                unwelcome surprise for anyone sitting at it. `core.reveal`
                handles the rest - the client names a model by category and
                filename and never supplies a path.
                """
                try:
                    from .core.reveal import locate_model, reveal

                    if request.remote not in ('127.0.0.1', '::1', 'localhost'):
                        return web.json_response(
                            {'success': False,
                             'error': 'Only available when ComfyUI runs on this machine'},
                            status=403)

                    data = await request.json()
                    path, problem = locate_model((data or {}).get('category'),
                                                 (data or {}).get('filename'))
                    if problem:
                        return web.json_response({'success': False, 'error': problem}, status=404)

                    opened, problem = reveal(path)
                    if not opened:
                        return web.json_response({'success': False, 'error': problem}, status=500)
                    return web.json_response({'success': True})
                except Exception as e:
                    self.logger.error(f"Model Linker reveal error: {e}", exc_info=True)
                    return web.json_response({'success': False, 'error': str(e)}, status=500)

            @routes.get("/model_linker/preview")
            async def get_model_preview(request):
                """Return a preview image/video for a model if one exists alongside it."""
                import os
                try:
                    model_type = request.rel_url.query.get('type', '')
                    model_file = request.rel_url.query.get('file', '')
                    if not model_type or not model_file:
                        return web.Response(status=400, text='type and file query params required')

                    import folder_paths as fp
                    model_path = fp.get_full_path(model_type, model_file)
                    if not model_path or not os.path.isfile(model_path):
                        return web.Response(status=404)

                    # Security: ensure resolved path is inside a known category directory
                    category_dirs = fp.get_folder_paths(model_type)
                    is_safe = False
                    for base_dir in category_dirs:
                        try:
                            real_model = os.path.realpath(model_path)
                            real_base = os.path.realpath(base_dir)
                            if os.path.commonpath([real_model, real_base]) == real_base:
                                is_safe = True
                                break
                        except (ValueError, OSError):
                            continue
                    if not is_safe:
                        return web.Response(status=403)

                    # Look for preview files with same base name
                    base_no_ext = os.path.splitext(model_path)[0]
                    for ext in ('.png', '.jpg', '.jpeg', '.webp', '.mp4'):
                        preview_path = base_no_ext + ext
                        if os.path.isfile(preview_path):
                            return web.FileResponse(preview_path)

                    return web.Response(status=404)
                except Exception as e:
                    self.logger.debug(f"Model Linker preview error: {e}")
                    return web.Response(status=404)

            # ==================== DOWNLOAD ROUTES ====================
            
            if download_available:
                
                @routes.post("/model_linker/search")
                async def search_sources(request):
                    """Search for model download sources."""
                    try:
                        data = await request.json()
                        filename = data.get('filename', '')
                        category = data.get('category', '')
                        
                        if not filename:
                            return web.json_response(
                                {'error': 'Filename is required'},
                                status=400
                            )
                        
                        results = {
                            'popular': None,
                            'model_list': None,
                            'huggingface': None,
                            'civitai': None,
                            'found': False
                        }
                        
                        # 1. Check popular models first (curated database)
                        popular_info = get_popular_model_url(filename)
                        if popular_info:
                            results['popular'] = {
                                'source': 'popular',
                                'filename': filename,
                                **popular_info
                            }
                            results['found'] = True
                        
                        # 2. Search model-list.json (ComfyUI Manager database with fuzzy matching)
                        if not results['found']:
                            model_list_result = search_model_list(filename)
                            if model_list_result:
                                results['model_list'] = model_list_result
                                results['found'] = True
                        
                        # 3. Search HuggingFace for exact file match
                        if not results['found']:
                            hf_result = search_huggingface_for_file(filename)
                            if hf_result:
                                results['huggingface'] = hf_result
                                results['found'] = True
                        
                        # 4. Search CivitAI for exact file match
                        if not results['found']:
                            civitai_result = search_civitai_for_file(filename)
                            if civitai_result:
                                results['civitai'] = civitai_result
                                results['found'] = True
                        
                        return web.json_response(results)
                        
                    except Exception as e:
                        self.logger.error(f"Model Linker search error: {e}", exc_info=True)
                        return web.json_response(
                            {'error': str(e)},
                            status=500
                        )
                
                @routes.post("/model_linker/download")
                async def download_model(request):
                    """Start downloading a model."""
                    try:
                        data = await request.json()
                        url = data.get('url', '')
                        filename = data.get('filename', '')
                        category = data.get('category', 'checkpoints')
                        subfolder = data.get('subfolder', '')
                        
                        if not url:
                            return web.json_response(
                                {'error': 'URL is required'},
                                status=400
                            )
                        
                        if not filename:
                            # Extract filename from URL
                            from urllib.parse import urlparse, unquote
                            parsed = urlparse(url)
                            filename = unquote(parsed.path.split('/')[-1])
                        
                        if not filename:
                            return web.json_response(
                                {'error': 'Could not determine filename'},
                                status=400
                            )
                        
                        # Build headers if needed
                        headers = {}
                        if 'huggingface.co' in url:
                            hf_token = data.get('hf_token', '')
                            if hf_token:
                                headers['Authorization'] = f'Bearer {hf_token}'
                        elif 'civitai.com' in url:
                            civitai_key = data.get('civitai_key', '')
                            if civitai_key and 'token=' not in url:
                                url += f"{'&' if '?' in url else '?'}token={civitai_key}"
                        
                        # Start background download
                        download_id = start_background_download(
                            url=url,
                            filename=filename,
                            category=category,
                            headers=headers if headers else None,
                            subfolder=subfolder
                        )
                        
                        return web.json_response({
                            'success': True,
                            'download_id': download_id,
                            'filename': filename,
                            'category': category
                        })
                        
                    except Exception as e:
                        self.logger.error(f"Model Linker download error: {e}", exc_info=True)
                        return web.json_response(
                            {'error': str(e), 'success': False},
                            status=500
                        )
                
                @routes.get("/model_linker/progress/{download_id}")
                async def get_download_progress(request):
                    """Get progress for a specific download."""
                    try:
                        download_id = request.match_info['download_id']
                        progress = get_progress(download_id)
                        
                        if progress:
                            return web.json_response(progress)
                        else:
                            return web.json_response(
                                {'error': 'Download not found'},
                                status=404
                            )
                    except Exception as e:
                        self.logger.error(f"Model Linker progress error: {e}", exc_info=True)
                        return web.json_response(
                            {'error': str(e)},
                            status=500
                        )
                
                @routes.get("/model_linker/progress")
                async def get_all_downloads_progress(request):
                    """Get progress for all downloads."""
                    try:
                        progress = get_all_progress()
                        return web.json_response(progress)
                    except Exception as e:
                        self.logger.error(f"Model Linker progress error: {e}", exc_info=True)
                        return web.json_response(
                            {'error': str(e)},
                            status=500
                        )
                
                @routes.post("/model_linker/cancel/{download_id}")
                async def cancel_download_route(request):
                    """Cancel a download in progress."""
                    try:
                        download_id = request.match_info['download_id']
                        cancel_download(download_id)
                        return web.json_response({'success': True})
                    except Exception as e:
                        self.logger.error(f"Model Linker cancel error: {e}", exc_info=True)
                        return web.json_response(
                            {'error': str(e), 'success': False},
                            status=500
                        )
                
                @routes.get("/model_linker/directories")
                async def get_directories(request):
                    """Get available model directories."""
                    try:
                        categories = [
                            'checkpoints', 'loras', 'vae', 'controlnet', 
                            'clip', 'clip_vision', 'embeddings', 'upscale_models',
                            'diffusion_models', 'text_encoders', 'ipadapter', 'sams'
                        ]
                        
                        directories = {}
                        for cat in categories:
                            path = get_download_directory(cat)
                            if path:
                                directories[cat] = path
                        
                        return web.json_response(directories)
                    except Exception as e:
                        self.logger.error(f"Model Linker directories error: {e}", exc_info=True)
                        return web.json_response(
                            {'error': str(e)},
                            status=500
                        )
            

            self.routes_setup = True
            self.logger.info("Model Linker: API routes registered successfully")
            return True
            
        except ImportError as e:
            self.logger.warning(f"Model Linker: Could not register routes (missing dependency): {e}")
            return False
        except Exception as e:
            self.logger.error(f"Model Linker: Error setting up routes: {e}", exc_info=True)
            return False


# Initialize the extension
try:
    extension = ModelLinkerExtension()
    extension.initialize()
except Exception as e:
    logging.error(f"ComfyUI Model Linker extension initialization failed: {e}", exc_info=True)
