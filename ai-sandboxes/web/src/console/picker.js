/* console/picker.js: provider + model picker (dropdown under "New task").
 * Split from the old console.js IIFE (rule 1), logic unchanged. The picker is
 * driven by the stored picks (localStorage ags-provider / ags-model), never by
 * the selected task.
 */
import { S, dom, esc, api, on} from './state.js';
import { createTask } from './dataflow.js';

/* ---------- target-cloud picker (rail) ---------- */

/* ---------- provider + model picker (dropdown under "New task") ---------- */

export function closeProvMenu() {
  if (dom.provMenu) dom.provMenu.hidden = true;
  dom.newBtn.setAttribute('aria-expanded', 'false');
  closeModelList();
}
function toggleProvMenu() {
  var open = dom.provMenu.hidden;
  dom.provMenu.hidden = !open;
  dom.newBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
  if (open) { renderProviders(); renderModels(); syncModelBtn(); }
}

export function renderProviders() {
  var provs = dom.provGrid.querySelectorAll('.prov');
  // The picker is always driven by the stored pick, never by the selected
  // task: otherwise a click updates localStorage but the highlight snaps
  // back to the task's provider, looking like the click did nothing.
  var current = localStorage.getItem('ags-provider') || 'azure';
  for (var i = 0; i < provs.length; i++) {
    provs[i].classList.toggle('on', provs[i].getAttribute('data-prov') === current);
  }
}

function modelName(id) {
  var m = S.availModels.find(function (x) { return x.id === id; });
  return m ? m.name : id;
}
export function syncModelBtn() { dom.modelCurrent.textContent = modelName(S.pickedModel); }

function closeModelList() {
  dom.modelList.hidden = true;
  dom.modelDd.classList.remove('open');
  dom.modelBtn.setAttribute('aria-expanded', 'false');
}
function toggleModelList() {
  var open = dom.modelList.hidden;
  dom.modelList.hidden = !open;
  dom.modelDd.classList.toggle('open', open);
  dom.modelBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
  if (open) renderModels();
}

export function renderModels() {
  if (!S.availModels.length) { dom.modelList.innerHTML = '<p class="p-empty">loading…</p>'; return; }
  dom.modelList.innerHTML = S.availModels.map(function (m) {
    return '<button class="model-opt' + (m.id === S.pickedModel ? ' on' : '') + '" data-model="' + esc(m.id) + '" type="button">' +
      '<span class="model-name">' + esc(m.name) + '</span>' +
      '<span class="model-blurb">' + esc(m.blurb) + '</span></button>';
  }).join('');
}

// Load the selectable planner models once.
api('/api/models').then(function (d) {
  S.availModels = d.models || [];
  if (!S.availModels.some(function (m) { return m.id === S.pickedModel; })) {
    S.pickedModel = S.availModels.length ? S.availModels[0].id : S.pickedModel;
  }
  syncModelBtn();
}).catch(function () {});

on(dom.provGrid, 'click', function (e) {
  var btn = e.target.closest('.prov');
  if (!btn) return;
  localStorage.setItem('ags-provider', btn.getAttribute('data-prov'));
  renderProviders();
});

on(dom.modelBtn, 'click', function (e) {
  e.stopPropagation();
  toggleModelList();
});

on(dom.modelList, 'click', function (e) {
  var btn = e.target.closest('[data-model]');
  if (!btn) return;
  S.pickedModel = btn.getAttribute('data-model');
  localStorage.setItem('ags-model', S.pickedModel);
  syncModelBtn();
  closeModelList();  // collapse back to the single-line selector
});

on(dom.createBtn, 'click', function () {
  var prov = localStorage.getItem('ags-provider') || 'azure';
  closeProvMenu();
  createTask(prov, S.pickedModel);
});

on(dom.newBtn, 'click', function (e) {
  e.stopPropagation();
  toggleProvMenu();
});

// Clicking anywhere outside the picker closes it.
document.addEventListener('click', function (e) {
  if (!dom.provMenu || dom.provMenu.hidden) return;
  // Use the menu itself, not e.target.closest(): clicking a model option
  // re-renders the list (innerHTML), which detaches the clicked node, so
  // closest() on that stale element would wrongly report "outside".
  if (dom.provMenu.contains(e.target) || e.target.closest('.new-btn')) return;
  closeProvMenu();
});
document.addEventListener('keydown', function (e) {
  if (e.key !== 'Escape') return;
  if (dom.modelList && !dom.modelList.hidden) { closeModelList(); return; }
  if (dom.provMenu && !dom.provMenu.hidden) closeProvMenu();
});
