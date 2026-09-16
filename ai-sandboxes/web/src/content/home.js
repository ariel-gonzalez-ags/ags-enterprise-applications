// All homepage copy lives here as structured data. Pages map over this file;
// editing copy never touches markup. Icon names map to components/Icon.astro.
export const brand = { name: 'Agisphire', year: 2026 };

export const nav = [
  { href: '#workloads', label: 'Workloads' },
  { href: '#how', label: 'How it works' },
  { href: '#demo', label: 'Sandbox proof' },
  { href: '#features', label: 'Capabilities' },
];

export const hero = {
  pill: 'AI-executed · sandbox-verified · delivered with proof',
  titleTop: 'Any cloud task.',
  titleEm: 'Done. Tested. Handed over.',
  sub: 'Harden a VM. Reproduce an outage. Stand up a reference architecture. Migrate a workload. If it needs cloud resources, our AI agents do it inside an isolated sandbox, and you only receive it once it\u2019s proven to work.',
  primary: { href: '#cta', label: 'Submit a task' },
  secondary: { href: '#demo', label: 'See a sandbox run' },
};

export const pipeline = {
  title: 'Live task pipeline',
  status: 'Verified · 38 checks passed',
  stages: [
    {
      icon: 'chat',
      title: 'Describe the work',
      desc: 'Natural language, a ticket, a diagram, or an architecture-center link. The planner agent turns it into an executable plan.',
      tag: '"harden this VM to CIS L2"',
    },
    {
      icon: 'box-check',
      title: 'Execute in a sandbox',
      desc: 'Agents provision real cloud resources in an ephemeral, isolated environment and carry out the task: build, fix, harden, migrate.',
      tag: 'ephemeral · isolated',
    },
    {
      icon: 'badge-check',
      title: 'Verify & hand over',
      desc: 'The result is tested against your acceptance criteria inside the sandbox. Only verified work is packaged and delivered.',
      tag: '38/38 checks green',
    },
  ],
  footLeft: 'task_id: ags_9d41ab · type: security-hardening · env: sandbox',
  footStatus: 'ready',
  footTime: '41m 07s',
};

export const workloads = {
  eyebrow: '// one platform, every workload',
  title: 'If it touches the cloud, it fits in a sandbox.',
  desc: 'Agisphire isn\u2019t one kind of tool. It\u2019s a place where any cloud task gets executed safely, tested honestly, and returned with evidence.',
  items: [
    {
      icon: 'grid',
      title: 'Architecture to MVP',
      desc: 'Point at an Azure Architecture Center pattern, an AWS reference, or your own diagram, and get a working, deployed implementation.',
      eg: '"build this hub-spoke topology as a runnable MVP"',
    },
    {
      icon: 'shield-check',
      title: 'Security hardening',
      desc: 'CIS benchmarks, baseline images, network lockdowns, applied and re-scanned in a sandbox before they ever touch your estate.',
      eg: '"harden this Ubuntu VM to CIS Level 2, prove the app still runs"',
    },
    {
      icon: 'search-clock',
      title: 'Troubleshooting & repro',
      desc: 'Describe the incident. Agents rebuild the failing environment in a sandbox, reproduce it, find the root cause, and verify the fix.',
      eg: '"our AKS pods OOM under load; reproduce and fix it"',
    },
    {
      icon: 'swap',
      title: 'Migration & modernization',
      desc: 'Replatform a service, move between clouds, or upgrade a runtime, rehearsed end-to-end in a sandbox before you commit.',
      eg: '"move this VM workload to containers on AWS"',
    },
    {
      icon: 'chart',
      title: 'Cost & performance experiments',
      desc: 'Benchmark SKUs, tune autoscaling, compare architectures, measured on real infrastructure, not guessed from pricing pages.',
      eg: '"which of these 3 setups survives 10x traffic cheapest?"',
    },
    {
      icon: 'bolt',
      title: 'DR & chaos drills',
      desc: 'Kill regions, corrupt data, expire certs, in a sandbox where failure is free. Delivered with a recovery runbook that actually ran.',
      eg: '"prove we can restore prod from backup in under 1 hour"',
    },
  ],
};

export const steps = {
  eyebrow: '// how it works',
  title: 'Three steps from request to result.',
  desc: 'No ticket queues, no six-week scoping docs. Describe the work, and our agent fleet executes it inside disposable cloud environments.',
  items: [
    {
      num: '01',
      icon: 'chat',
      title: 'Describe the work',
      desc: 'A sentence, a Jira ticket, an architecture link, a diagram, a compliance requirement. The planner agent decomposes it into a concrete execution plan.',
    },
    {
      num: '02',
      icon: 'box-check',
      title: 'Agents execute in a sandbox',
      desc: 'Real cloud resources are provisioned in an ephemeral, isolated environment. Specialized LLM agents do the work (build, fix, harden, migrate) with full credentials scoped to the sandbox only.',
    },
    {
      num: '03',
      icon: 'badge-check',
      title: 'Verified, then handed over',
      desc: 'The outcome is tested against your acceptance criteria: smoke tests, benchmarks, security scans. You get the artifacts, the scripts, and the evidence.',
    },
  ],
};

export const demo = {
  eyebrow: '// proof, not promises',
  title: 'Nothing ships until the sandbox says so.',
  desc: 'A real task report: request in, verified result out.',
  path: 'agisphire / sandbox / ags_9d41ab',
  inputTitle: 'Input · the request',
  inputs: [
    {
      icon: 'chat',
      title: '"Harden our Ubuntu 22.04 VM baseline to CIS Level 2, and prove our app still works on it."',
      meta: 'natural language · submitted via web',
    },
    {
      icon: 'download',
      title: 'Attachments: current VM image spec + app health-check endpoints',
      meta: '2 files · parsed by planner agent',
    },
    {
      icon: 'box-check',
      title: 'Environment: sandbox auto-provisioned (Azure, eu-west)',
      meta: 'scoped credentials · destroyed after handover',
    },
  ],
  outputTitle: 'Output · sandbox verification',
  checks: [
    { text: 'CIS Level 2 applied', code: '· 218 controls · remediated via Ansible' },
    { text: 'Compliance re-scan', code: '· 94/96 pass · 2 documented exceptions' },
    { text: 'App smoke tests on hardened image', code: '· 12/12 endpoints healthy' },
    { text: 'Regression check', code: '· boot +28ms · no service failures' },
    { text: 'Deliverables packaged', code: '· hardened image + playbook + scan report' },
  ],
  verdict: 'Ready for handover',
  verdictMeta: 'verified 4m ago',
};

export const features = {
  eyebrow: '// capabilities',
  title: 'Built for teams who can\u2019t afford guesswork.',
  items: [
    {
      icon: 'box-check',
      title: 'True isolation',
      desc: 'Every task runs in an ephemeral sandbox with its own identity, network, and lifecycle. Experiments and failures never reach your production estate.',
    },
    {
      icon: 'clock',
      title: 'Hours, not sprints',
      desc: 'From request to verified result in a single session. Iterate by chatting with the agents and re-running the sandbox.',
    },
    {
      icon: 'shield',
      title: 'Evidence by default',
      desc: 'Every delivery includes the test report, the logs, and the exact commands that ran. "It works" is a claim; the sandbox report is the proof.',
    },
    {
      icon: 'network',
      title: 'Any cloud, any stack',
      desc: 'Azure, AWS, GCP, on-prem, or hybrid. The same request-to-result flow works across providers, with native tooling for each target.',
    },
    {
      icon: 'doc',
      title: 'Readable output',
      desc: 'Typed code, documented IaC, runbooks, and the verification report. Everything an engineer needs to own the result. No black boxes.',
    },
    {
      icon: 'users',
      title: 'Human in the loop',
      desc: 'Approve each stage (plan, execute, verify) or let the fleet run autonomously. Destructive actions always require sign-off. Your call, per task.',
    },
  ],
};

export const stats = [
  { n: '58', unit: 'm', label: 'median request → verified handover' },
  { n: '4,320', unit: '+', label: 'sandbox tasks executed' },
  { n: '99.1', unit: '%', label: 'deliveries passing acceptance first time' },
  { n: '0', unit: '', label: 'production incidents caused' },
];

export const cta = {
  title: 'Ship the work, not the ticket.',
  sub: 'Describe the task. Receive it tested, with proof.',
  button: 'Submit your first task',
};

export const footer = {
  note: `\u00a9 ${brand.year} ${brand.name}. Concept mockup`,
  links: [
    { href: '#workloads', label: 'Workloads' },
    { href: '#how', label: 'How it works' },
    { href: '#demo', label: 'Sandbox proof' },
    { href: '#cta', label: 'Get started' },
  ],
};
