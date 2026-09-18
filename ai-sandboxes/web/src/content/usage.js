// /usage page copy. UI shells map over this; live numbers come from
// /api/embers. Edit text here, never in UsagePanel.astro.

export const usage = {
  eyebrow: 'Usage',
  title: "What you've consumed",
  // `Embers` stays inline-bold via the component (it wraps the unit name).
  ledePre: 'Every sandbox run burns',
  ledeUnit: 'Embers',
  ledePost: 'from compute time and model tokens. This is your balance and where it went.',
  unit: 'Embers',
  topup: 'Top up · soon',
  topupTitle: 'Top-up arrives with billing (soon)',
  stats: { runs: 'Runs', compute: 'Compute', tokens: 'Tokens', spent: 'Spent' },
  byModel: 'By model',
  history: 'History',
  allTime: 'All time',
  emptyAll: 'No usage yet. Run a sandbox to see it here.',
  emptyPeriod: 'No usage data for this period.',
  emptyModel: 'No model usage in this period.',
  noAllowance: 'No allowance yet',
  loadError: 'Could not load usage. Try refreshing.',
};
