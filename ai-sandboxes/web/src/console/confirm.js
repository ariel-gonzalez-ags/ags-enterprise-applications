/* console/confirm.js: the shared design-system confirm dialog (not
 * window.confirm). Title + action label are set per call (delete vs stop-run
 * read very differently). DOM handles live in state.js (dom.confirm*); the
 * pending callback is S.confirmCb. Split from console.js (rule 1), logic
 * unchanged.
 */
import { S, dom, on} from './state.js';

/* Generic confirm. The dialog is shared, so the title and the action
 * button's label are set per call (delete vs stop-run read very differently). */
export function askConfirm(body, onYes, title, goLabel) {
  dom.confirmTitle.textContent = title || 'Delete task?';
  dom.confirmBody.textContent = body;
  dom.confirmGo.textContent = goLabel || 'Delete';
  S.confirmCb = onYes;
  dom.confirmEl.hidden = false;
}
export function closeConfirm() { dom.confirmEl.hidden = true; S.confirmCb = null; }
on(dom.confirmGo, 'click', function () { var cb = S.confirmCb; closeConfirm(); if (cb) cb(); });
on(dom.confirmCancel, 'click', closeConfirm);
on(dom.confirmEl, 'click', function (e) { if (e.target === dom.confirmEl) closeConfirm(); });
