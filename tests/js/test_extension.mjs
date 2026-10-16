// How the extension registers itself with the ComfyUI frontend.
import { createChecker, loadExtension, loadModule, readAllSources, readSource } from './harness.mjs';
import { app } from './stubs/scripts/app.js';

export const name = 'extension registration';

export default async function run() {
  const check = createChecker(name);
  const config = await loadExtension();
  // Bans on imports and blocking dialogs apply to every module, not just
  // the entry point - splitting the file is precisely what would let one
  // slip back in somewhere else.
  const source = readAllSources();

  check('the module evaluates and registers an extension', !!config,
        config ? `name=${config.name}` : 'registerExtension was never called');
  if (!config) return check.failures;

  check('a command is registered', config.commands?.[0]?.id === 'model-linker-open');
  check('the command appears in a menu',
        config.menuCommands?.[0]?.commands?.includes('model-linker-open'),
        JSON.stringify(config.menuCommands?.[0]?.path));
  check('a keybinding is registered',
        config.keybindings?.[0]?.commandId === 'model-linker-open',
        JSON.stringify(config.keybindings?.[0]?.combo));
  check('the canvas right-click menu offers it',
        config.getCanvasMenuItems?.()?.[0]?.content?.includes('Model Linker'));
  check('setup is a function', typeof config.setup === 'function');

  // scripts/ui.js is deprecated and announced for removal in frontend v1.34;
  // $el is defined locally instead.
  // Anchored to real import statements: the source legitimately *mentions*
  // ui.js in the comment explaining why $el is defined locally instead.
  check('the deprecated ui.js shim is not imported',
        !/^\s*import\b[^\n]*scripts\/ui\.js/m.test(source));
  check('only app.js and api.js are imported from ComfyUI',
        (source.match(/^import .*scripts\/(\w+)\.js/gm) || [])
          .every(line => /scripts\/(app|api)\.js/.test(line)));

  // alert()/confirm() block the whole page; errors go through the toast API
  check('no blocking dialogs are used',
        !/(^|[^.\w])(alert|confirm)\s*\(/m.test(source.replace(/\/\/.*$/gm, '')));

  // The floating button was removed on request; the menu and keybinding remain
  check('no floating-button setting is registered',
        !config.settings?.some?.(setting => /FloatingButton/.test(setting.id)),
        JSON.stringify(config.settings ?? null));
  check('no floating-button code remains',
        !/createFloatingButton|linkerButton|model-linker-button/.test(source));

  // The dialog, its preview tooltip and notifications legitimately attach to
  // the page. What must not come back is scraping ComfyUI's own Vue-rendered
  // markup for somewhere to inject a persistent control.
  check('ComfyUI markup is not scraped for an injection point',
        !/\[class\*=.menu.\]|app\.menu\?\.settingsGroup/.test(source));

  check('a loaded workflow is checked through the extension hook',
        typeof config.afterConfigureGraph === 'function');
  const openSetting = config.settings?.find?.(s => s.id === 'ModelLinker.OpenOnMissing');
  check('opening on load is an opt-in setting', openSetting?.type === 'boolean' && openSetting?.defaultValue === false);
  check('ComfyUI\'s Missing Models popup is not observed',
        !/MutationObserver/.test(readAllSources()));
  check('the same workflow is announced once per session', /notified\.has\(/.test(source));
  check('a workflow loaded before setup is still checked',
        /if \(!this\.dialog\) this\.dialog = new LinkerManagerDialog\(\);\s*if \(this\.dialog\.isOpen\(\)\) return;/.test(source));
  // Deviation from the brief's literal snippet: the brief's dedupe alone
  // does not stop a slow analysis from toasting about a workflow another
  // tab has since replaced, so a generation counter guards it the same way
  // loadWorkflowData drops a superseded analysis via analyzeAbort.
  check('a superseded background analysis is dropped',
        /generation !== this\.loadGeneration/.test(source));

  // --- per-workflow dedupe round 1 fix: app.graph.serialize() has no id for
  // the open tab (LiteGraph's serialized graph carries none), so the dedupe
  // signature used to be keyed on an always-empty string, colliding every
  // workflow with the same missing set into one notification. The fix reads
  // the frontend's own workflow store, which keys every open tab by its own
  // (disambiguated) path. ---
  const linkerSource = readSource('linker.js');
  check('the dead workflow.id fallback is gone', !/workflow\.id/.test(linkerSource));
  check('the dedupe key comes from the workflow store, not the serialized graph',
        /app\.extensionManager\?\.workflow\?\.activeWorkflow\?\.path/.test(linkerSource));
  check('a background-check failure is logged quietly on both paths',
        (linkerSource.match(/console\.debug\(.Model Linker: background check failed./g) || []).length === 2);

  // Behavioural: stub the workflow store on the real app stub (the same
  // object linker.js's "../../scripts/app.js" import resolves to) and drive
  // the real reader (workflowKey()) and the real dedupe (announce()).
  // ModelLinker is exported for exactly this - its own dialog can't be
  // constructed here, since LinkerManagerDialog's constructor needs a DOM
  // this harness has none of.
  const linkerModule = await loadModule('linker.js');
  check('ModelLinker is exported', typeof linkerModule.ModelLinker === 'function');
  if (typeof linkerModule.ModelLinker === 'function') {
    const { ModelLinker } = linkerModule;
    const hadWorkflowStore = Object.prototype.hasOwnProperty.call(app.extensionManager, 'workflow');
    const originalWorkflowStore = app.extensionManager.workflow;
    const originalToasts = globalThis.__toasts;
    try {
      const data = { total_missing: 1, missing_models: [{ original_path: 'model.safetensors' }] };

      globalThis.__toasts = [];
      const linkerA = new ModelLinker();
      app.extensionManager.workflow = { activeWorkflow: { path: 'workflows/a.json' } };
      linkerA.announce(linkerA.workflowKey(), data);
      app.extensionManager.workflow = { activeWorkflow: { path: 'workflows/b.json' } };
      linkerA.announce(linkerA.workflowKey(), data);
      check('two different workflow identities with the same missing set both notify',
            globalThis.__toasts.length === 2);

      globalThis.__toasts = [];
      const linkerB = new ModelLinker();
      app.extensionManager.workflow = { activeWorkflow: { path: 'workflows/c.json' } };
      linkerB.announce(linkerB.workflowKey(), data);
      linkerB.announce(linkerB.workflowKey(), data);
      check('the same identity twice notifies once', globalThis.__toasts.length === 1);
    } finally {
      if (hadWorkflowStore) app.extensionManager.workflow = originalWorkflowStore;
      else delete app.extensionManager.workflow;
      globalThis.__toasts = originalToasts;
    }
  }

  return check.failures;
}
