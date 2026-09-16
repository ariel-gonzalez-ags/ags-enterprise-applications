// Console data model + mock content. This file defines the vocabulary the
// product speaks: task states, providers, output formats, run results.
// UI components map over this — adding a provider/format is a data edit.

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

// Task lifecycle. Order matters — the UI renders them as a pipeline.
export const taskStates = [
  { id: 'drafting', label: 'Drafting', hint: 'chatting with the planner' },
  { id: 'planned', label: 'Planned', hint: 'execution plan ready' },
  { id: 'running', label: 'Running', hint: 'agents working in sandbox' },
  { id: 'verified', label: 'Verified', hint: 'checks green, awaiting handover' },
  { id: 'delivered', label: 'Delivered', hint: 'artifacts in library' },
];

// Mock library entries — what a returning user's history looks like.
export const tasks = [
  {
    id: 'ags_9d41ab',
    title: 'Harden Ubuntu 22.04 baseline to CIS L2',
    state: 'verified',
    provider: 'azure',
    formats: ['ansible', 'markdown'],
    idempotent: true,
    checks: { passed: 38, total: 38 },
    updated: '4m ago',
  },
  {
    id: 'ags_71c0e2',
    title: 'Hub-spoke network MVP from architecture link',
    state: 'delivered',
    provider: 'azure',
    formats: ['terraform', 'markdown'],
    idempotent: true,
    checks: { passed: 21, total: 21 },
    updated: '2d ago',
  },
  {
    id: 'ags_b3f58c',
    title: 'Reproduce AKS OOMKill under load + fix',
    state: 'running',
    provider: 'gcp',
    formats: ['bash', 'markdown'],
    idempotent: false,
    checks: { passed: 7, total: 12 },
    updated: 'running · 18m',
  },
  {
    id: 'ags_5501da',
    title: 'EC2 → containers migration rehearsal',
    state: 'planned',
    provider: 'aws',
    formats: ['terraform'],
    idempotent: true,
    checks: { passed: 0, total: 9 },
    updated: '1h ago',
  },
];

// Mock conversation for the brainstorm surface — shows the planner agent
// at work: user intent in, clarifying questions + plan out.
export const brainstorm = [
  {
    role: 'user',
    text: 'I need a cost-optimal way to run our nightly ETL. It spikes to 40 parallel workers for ~50 minutes, then nothing.',
  },
  {
    role: 'agent',
    agent: 'planner',
    text: 'Got it. A few things shape the plan:\n\n1. Is the workload containerized today, or VMs?\n2. Any state between workers, or embarrassingly parallel?\n3. Preferred cloud — or should I benchmark the same job on spot fleets across providers?',
  },
  {
    role: 'user',
    text: 'Docker images already, fully parallel, no shared state. Benchmark across AWS and Azure — I want proof, not pricing-page math.',
  },
  {
    role: 'agent',
    agent: 'planner',
    text: 'Plan drafted: provision identical sandboxes on AWS + Azure, run your image on spot capacity with 40 workers, measure wall time and cost per run, then tear down. Deliverable: Terraform for the winner + a Markdown report with the benchmark evidence. Estimated sandbox time: ~2h. Approve to execute?',
  },
];

// Mock artifacts for the run view — the "proof" attached to a verified task.
export const artifacts = [
  { id: 'main.tf', kind: 'terraform', size: '8.2 KB', note: 'winner: AWS spot fleet, fully idempotent' },
  { id: 'variables.tf', kind: 'terraform', size: '1.1 KB', note: 'region, worker count, image tag' },
  { id: 'benchmark-report.md', kind: 'markdown', size: '14.6 KB', note: 'AWS $0.41/run vs Azure $0.63/run, p95 47m' },
  { id: 'smoke-test.sh', kind: 'bash', size: '0.9 KB', note: 'rerun the acceptance checks anywhere' },
];

// Run config defaults — what the inspector pre-selects for a new task.
export const defaultRunConfig = {
  providers: ['azure'],
  formats: ['terraform', 'markdown'],
  idempotent: true,
  destroyAfter: true,
  maxHours: 4,
};
