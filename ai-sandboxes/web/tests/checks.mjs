// Smoke tests for the built site. Run: node tests/checks.mjs
// These run inside the Docker build (see ../../Dockerfile) — a failing check
// fails the image build, so nothing broken can ship.
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const dist = join(dirname(fileURLToPath(import.meta.url)), '..', 'dist');
const read = (p) => {
  const f = join(dist, p);
  if (!existsSync(f)) fail(`missing file: dist/${p}`);
  return readFileSync(f, 'utf8');
};

let failures = 0;
function fail(msg) {
  failures++;
  console.error(`FAIL  ${msg}`);
}
function pass(msg) {
  console.log(`ok    ${msg}`);
}
function check(desc, cond) {
  cond ? pass(desc) : fail(desc);
}

// ---- homepage ----
const home = read('index.html');
check('home: has <title>', home.includes('<title>Agisphire'));
check('home: nav renders brand name', home.includes('Agisphire'));
check('home: hero headline present', home.includes('Any cloud task.') && home.includes('Done. Tested. Handed over.'));
check('home: logo inline SVG (twin-arc ring)', (home.match(/M14 50 A36 36/g) || []).length >= 2);
check('home: logo counter A present', home.includes('M50 43 L53.8 54'));
check('home: workloads section', home.includes('id="workloads"') && home.includes('Security hardening'));
check('home: how-it-works section', home.includes('id="how"'));
check('home: demo section', home.includes('id="demo"') && home.includes('CIS Level 2 applied'));
check('home: features section', home.includes('id="features"') && home.includes('True isolation'));
check('home: stats section', home.includes('median request') && home.includes('4,320'));
check('home: CTA section', home.includes('id="cta"') && home.includes('Ship the work'));
check('home: footer note', home.includes('concept mockup'));
// Astro may minify/lowercase inlined CSS differently across versions —
// match tokens tolerantly rather than asserting an exact byte sequence.
check('home: design tokens inlined', /--accent\s*:\s*#f05623/i.test(home));
check('home: favicon points to /assets/logo.svg', home.includes('/assets/logo.svg'));
check('home: no leftover template artifacts', !home.includes('Astro.props') && !home.includes('{hero.'));

// ---- auth wiring ----
check('home: sign-in points at /api/auth/login', home.includes('/api/auth/login'));
check('home: auth.js included (Base auth prop)', home.includes('/js/auth.js'));
check('home: hidden console link for signed-in users', home.includes('data-auth="protected-link"'));

const app = read('app/index.html');
check('app: protected placeholder renders', app.includes('Your sandbox console'));
check('app: gate script calls /api/auth/me', app.includes('/api/auth/me'));
check('app: login button targets next=/app', app.includes('/api/auth/login?next=/app'));

const authJs = read('js/auth.js');
check('asset: auth.js uses /api/auth/me', authJs.includes('/api/auth/me'));
check('asset: auth.js is an IIFE', authJs.includes('(function () {'));

// ---- static assets ----
const logo = read('assets/logo.svg');
check('asset: logo.svg has ember ring', /#f05623/i.test(logo));
check('asset: logo.svg has counter A', logo.includes('M50 43 L53.8 54'));
read('assets/logo-lockup.svg');
pass('asset: logo-lockup.svg exists');

if (failures > 0) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log('\nAll checks passed');
