/**
 * Ask Jev about two sample approval requests, before the agent relies on it.
 *
 *     bun run jev
 *
 * Both are checked against the same sample loan record, the shape `LoanDesk_GetLoan` returns.
 * The first states only what the record shows and should pass; the second carries the borrower's
 * tax ID and bank account and should be blocked. Nothing is sent anywhere but TypeSafe.
 */
import { MAX_LEAK, MIN_GROUNDED, check, jevEnabled } from "./src/mastra/jev";

if (!jevEnabled()) {
  console.error("TYPESAFE_API_KEY is not set: put your TypeSafe key in agent/.env.");
  process.exit(2);
}

const record = {
  id: "LN-2292",
  borrower: { business_name: "Harbor Street Bakery", contact_email: "owner@harborstreetbakery.example" },
  amount: 95000,
  purpose: "Second oven and storefront lease",
  credit_score: 712,
  annual_revenue: 640000,
  years_in_business: 6,
  bank_account_number: "000123456789",
  tax_id: "12-3456789",
  status: "pending",
};

const samples = [
  ["clean", "Harbor Street Bakery (LN-2292) asks for $95,000 for a second oven and a storefront lease. Credit score 712, $640K annual revenue, six years in business."],
  ["leaks", "Please approve LN-2292 for $95,000. Harbor Street Bakery, tax ID 12-3456789, pays from account 000123456789. Credit score 712."],
] as const;

console.log(`Thresholds: grounded at least ${MIN_GROUNDED}, sensitive-data leak at most ${MAX_LEAK}.\n`);
for (const [label, message] of samples) {
  const verdict = await check(message, [record]);
  console.log(`${label.padEnd(6)} ${verdict.ok ? `allowed grounded ${verdict.grounded.toFixed(2)}, leak ${verdict.leak.toFixed(2)}` : `blocked ${verdict.reason}`}`);
}
