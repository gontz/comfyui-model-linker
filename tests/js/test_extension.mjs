// How the extension registers itself with the ComfyUI frontend.
import { createChecker, loadExtension, readAllSources } from './harness.mjs';

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

  return check.failures;
}
