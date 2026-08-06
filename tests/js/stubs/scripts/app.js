// Stand-in for ComfyUI's app module. linker.js imports "../../scripts/app.js",
// so the file under test is copied to stubs/extensions/<name>/linker.js and
// that relative path lands here.
export const app = {
  registerExtension(config) { globalThis.__registered = config; },
  extensionManager: {
    setting: { get: () => undefined, set: async () => {} },
    toast: { add: (toast) => (globalThis.__toasts ||= []).push(toast) },
    registerSidebarTab: () => {},
  },
  graph: null,
  canvas: null,
};
