// Console vocabulary: the product's shared language. UI shells and
// public/js/console.js map over this; live data comes from /api/tasks*.
// Adding a provider/format/state is a data edit.

export const providers = [
  { id: 'azure', name: 'Azure', color: '#0078D4' },
  { id: 'aws', name: 'AWS', color: '#FF9900' },
  { id: 'gcp', name: 'GCP', color: '#4285F4' },
];

export const outputFormats = [
  { id: 'terraform', label: 'Terraform', kind: 'iac' },
  { id: 'ansible', label: 'Ansible', kind: 'iac' },
  { id: 'arm', label: 'ARM / Bicep', kind: 'iac' },
  { id: 'bash', label: 'Bash', kind: 'script' },
  { id: 'powershell', label: 'PowerShell', kind: 'script' },
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
