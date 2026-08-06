// Runs every frontend suite. Exits non-zero if anything failed.
//
//   node tests/js/run.mjs

import { readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const suites = readdirSync(HERE).filter(f => f.startsWith('test_') && f.endsWith('.mjs')).sort();

const failures = [];
for (const file of suites) {
  const suite = await import('file://' + join(HERE, file).replace(/\\/g, '/'));
  console.log(`\n${suite.name || file}`);
  failures.push(...await suite.default());
}

console.log('');
if (failures.length) {
  console.log(`${failures.length} frontend check(s) failed:`);
  for (const failure of failures) console.log(`  - ${failure}`);
  process.exit(1);
}
console.log('All frontend checks passed.');
