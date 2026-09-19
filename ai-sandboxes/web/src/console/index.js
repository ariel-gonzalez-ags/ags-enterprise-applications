/* console/index.js: entry point for the /app console. Split from the old
 * console.js IIFE (rule 1): importing the feature modules runs their listener
 * registrations (and picker.js's /api/models loader) at evaluation time, in
 * this order; the boot block then mirrors the original lines 939-947. All
 * cross-module calls happen at runtime (listeners, the boot), so the import
 * cycles between dataflow and rail/thread/inspector/picker are safe.
 */
import { S, list } from './state.js';
import './confirm.js';
import './rail.js';
import './thread.js';
import './inspector.js';
import './picker.js';
import './palette.js';
import { renderAll, refreshList, select } from './dataflow.js';
import { loadEmbers } from './inspector.js';

/* ---------- boot ---------- */

// Called by the /app gate (pages/app.astro) only after auth confirms a user, so
// anonymous visitors never boot the console. Guarded on list so importing the
// module off-/app is a no-op.
export function boot() {
  if (!list) return; // not on /app: nothing to do
  loadEmbers();
  refreshList().then(function () {
    if (S.tasks.length) select(S.tasks[0].id);
    else renderAll();
  }).catch(function () {
    list.innerHTML = '<p class="empty mono">Sign in to see your tasks.</p>';
  });
}
