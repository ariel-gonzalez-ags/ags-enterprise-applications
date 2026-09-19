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

if (list) { // not on /app: the imported DOM refs are null-guarded below
  loadEmbers();
  refreshList().then(function () {
    if (S.tasks.length) select(S.tasks[0].id);
    else renderAll();
  }).catch(function () {
    list.innerHTML = '<p class="empty mono">Sign in to see your tasks.</p>';
  });
}
