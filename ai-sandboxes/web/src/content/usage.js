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
  // Top-up (Stripe). The panel maps over `packs`; custom amount has a $ minimum.
  topupTitle: 'Buy Embers',
  topupNote: 'A purchase, separate from the free trial. Embers never expire. 1 Ember = $0.01. Secure checkout by Stripe; we never see your card.',
  topupCustomLabel: 'Custom amount (USD)',
  topupCustomPlaceholder: '25',
  topupGo: 'Continue to checkout',
  topupMinError: 'Minimum is $',
  packs: [
    { usd: 10, label: '$10' },
    { usd: 25, label: '$25' },
    { usd: 50, label: '$50' },
  ],
  // Card gate: shown to a new user with no card on file (trial not yet unlocked).
  cardGateTitle: 'Unlock your trial',
  cardGateBody: 'Add a card to start your trial allowance. It is not charged; it just keeps one account per person. Secure setup by Stripe.',
  cardGateGo: 'Add a card',
  cardGateDone: 'Card on file',
  stats: { runs: 'Runs', compute: 'Compute', tokens: 'Tokens', spent: 'Spent' },
  byModel: 'By model',
  history: 'History',
  allTime: 'All time',
  emptyAll: 'No usage yet. Run a sandbox to see it here.',
  emptyPeriod: 'No usage data for this period.',
  emptyModel: 'No model usage in this period.',
  noAllowance: 'No allowance yet',
  loadError: 'Could not load usage. Try refreshing.',
  billingOff: 'Billing is not enabled yet.',
};
