// Console vocabulary: the product's shared language. UI shells and
// public/js/console.js map over this; live data comes from /api/tasks*.
// Adding a provider/format/state is a data edit.

export const providers = [
  { id: 'azure', name: 'Azure', color: '#0078D4' },
  { id: 'aws', name: 'AWS', color: '#FF9900' },
  { id: 'gcp', name: 'GCP', color: '#4285F4' },
];

// Canonical deliverable catalog. This is the vocabulary the planner PREFERS
// when proposing a plan: it drives the chip labels/kinds (below), the
// artifact filenames (runner), and the planner prompt. It is a preference,
// not a constraint: the user can always add a custom deliverable and the
// planner resolves it (normalize_format) instead of rejecting it. Adding a
// known format is a data edit here + a filename in runner._FORMAT_FILES.
export const outputFormats = [
  { id: 'terraform', label: 'Terraform', kind: 'iac' },
  { id: 'ansible', label: 'Ansible', kind: 'iac' },
  { id: 'arm', label: 'ARM / Bicep', kind: 'iac' },
  { id: 'helm', label: 'Helm chart', kind: 'iac' },
  { id: 'kubernetes', label: 'Kubernetes manifests', kind: 'iac' },
  { id: 'dockerfile', label: 'Dockerfile', kind: 'iac' },
  { id: 'bash', label: 'Bash', kind: 'script' },
  { id: 'powershell', label: 'PowerShell', kind: 'script' },
  { id: 'python', label: 'Python', kind: 'script' },
  { id: 'json', label: 'JSON policy', kind: 'config' },
  { id: 'yaml', label: 'YAML config', kind: 'config' },
  { id: 'markdown', label: 'Markdown runbook', kind: 'doc' },
];

// Task lifecycle. Order matters: the UI renders them as a pipeline.
export const taskStates = [
  { id: 'drafting', label: 'Drafting', hint: 'chatting with the planner' },
  { id: 'planned', label: 'Planned', hint: 'execution plan ready' },
  { id: 'running', label: 'Running', hint: 'agents working in sandbox' },
  { id: 'verified', label: 'Verified', hint: 'checks green, awaiting handover' },
  { id: 'delivered', label: 'Delivered', hint: 'artifacts in library' },
];
