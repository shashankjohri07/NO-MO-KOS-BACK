/**
 * Subscription billing, Spotify-style: the admin defines plans (price, period,
 * document quota, which tools are included); users buy one through Razorpay;
 * access stops automatically the moment expires_at passes — no cron needed,
 * entitlement is computed lazily at read time.
 *
 * Everything the admin can touch lives in app_config('billing_config'):
 *   {
 *     enabled: boolean,             // master switch — off = everything free
 *     keyId: 'rzp_test_…',          // Razorpay key id (public)
 *     keySecret: '…',               // Razorpay key secret (write-only)
 *     plans: [{ id, name, description, priceInr, periodDays,
 *               docsPerPeriod, tools: string[] }]
 *   }
 *
 * A plan with priceInr === 0 is the free tier every signed-in user starts on.
 */

import crypto from 'crypto';
import type { Store } from './store';

export interface BillingPlan {
  id: string;
  name: string;
  description: string;
  priceInr: number; // rupees; 0 = free tier
  periodDays: number; // subscription length (free tier: quota window)
  docsPerPeriod: number; // -1 = unlimited
  tools: string[]; // product keys the plan may use
}

export interface BillingConfig {
  enabled: boolean;
  keyId: string;
  keySecret: string;
  plans: BillingPlan[];
}

/** All tool keys, for "everything included" defaults. */
export const ALL_TOOLS = [
  'document-prep',
  'page-numbering',
  'annexures',
  'signatures',
  'bookmarks',
  'index-generator',
];

export const DEFAULT_PLANS: BillingPlan[] = [
  {
    id: 'free',
    name: 'Free',
    description: 'Try every tool with a monthly document allowance.',
    priceInr: 0,
    periodDays: 30,
    docsPerPeriod: 5,
    tools: ALL_TOOLS,
  },
  {
    id: 'pro',
    name: 'Pro',
    description: 'For individual advocates — generous monthly quota.',
    priceInr: 499,
    periodDays: 30,
    docsPerPeriod: 100,
    tools: ALL_TOOLS,
  },
  {
    id: 'unlimited',
    name: 'Unlimited',
    description: 'For firms — no document limits.',
    priceInr: 1999,
    periodDays: 30,
    docsPerPeriod: -1,
    tools: ALL_TOOLS,
  },
];

export async function loadBillingConfig(store: Store): Promise<BillingConfig> {
  const raw = ((await store.getConfig('billing_config')) ?? {}) as Partial<BillingConfig>;
  return {
    enabled: Boolean(raw.enabled),
    keyId: raw.keyId || '',
    keySecret: raw.keySecret || '',
    plans: Array.isArray(raw.plans) && raw.plans.length > 0 ? raw.plans : DEFAULT_PLANS,
  };
}

/** What the signed-in user is entitled to right now. */
export interface Entitlement {
  billingEnabled: boolean;
  plan: BillingPlan;
  /** ISO expiry of a PAID subscription; null on the free tier. */
  expiresAt: string | null;
  docsUsed: number;
  /** -1 = unlimited */
  docsLimit: number;
  remaining: number; // -1 = unlimited
}

export async function getEntitlement(store: Store, email: string): Promise<Entitlement> {
  const cfg = await loadBillingConfig(store);
  const freePlan =
    cfg.plans.find((p) => p.priceInr === 0) ?? { ...DEFAULT_PLANS[0], tools: ALL_TOOLS };

  if (!cfg.enabled) {
    // Billing off = everything unlimited, like today.
    return {
      billingEnabled: false,
      plan: { ...freePlan, docsPerPeriod: -1 },
      expiresAt: null,
      docsUsed: 0,
      docsLimit: -1,
      remaining: -1,
    };
  }

  const sub = await store.getActiveSubscription(email);
  const plan = (sub && cfg.plans.find((p) => p.id === sub.plan_id)) || freePlan;
  // Quota window: paid = the subscription period; free = a rolling window of
  // the plan's periodDays.
  const windowStart = sub
    ? sub.started_at
    : new Date(Date.now() - plan.periodDays * 86400_000).toISOString();
  const docsUsed = plan.docsPerPeriod === -1 ? 0 : await store.countUsageSince(email, windowStart);
  const remaining = plan.docsPerPeriod === -1 ? -1 : Math.max(0, plan.docsPerPeriod - docsUsed);

  return {
    billingEnabled: true,
    plan,
    expiresAt: sub ? sub.expires_at : null,
    docsUsed,
    docsLimit: plan.docsPerPeriod,
    remaining,
  };
}

// ── Razorpay (plain fetch, no SDK) ─────────────────────────────────────────

const RZP_API = 'https://api.razorpay.com/v1';

function rzpAuth(cfg: BillingConfig): string {
  return 'Basic ' + Buffer.from(`${cfg.keyId}:${cfg.keySecret}`).toString('base64');
}

/** Create a Razorpay order for one period of the plan. Amount is in paise. */
export async function createOrder(
  cfg: BillingConfig,
  plan: BillingPlan,
  email: string,
): Promise<{ orderId: string; amount: number; currency: string }> {
  const r = await fetch(`${RZP_API}/orders`, {
    method: 'POST',
    headers: { Authorization: rzpAuth(cfg), 'Content-Type': 'application/json' },
    body: JSON.stringify({
      amount: plan.priceInr * 100,
      currency: 'INR',
      receipt: `nomikos_${plan.id}_${Date.now()}`,
      notes: { email, plan_id: plan.id },
    }),
    signal: AbortSignal.timeout(20_000),
  });
  if (!r.ok) {
    const body = await r.text();
    throw new Error(`razorpay order failed: ${r.status} ${body.slice(0, 300)}`);
  }
  const order: any = await r.json();
  return { orderId: order.id, amount: order.amount, currency: order.currency };
}

/** Razorpay checkout success handler: verify the payment signature
 * (HMAC-SHA256 of "orderId|paymentId" keyed with the secret). */
export function verifySignature(
  cfg: BillingConfig,
  orderId: string,
  paymentId: string,
  signature: string,
): boolean {
  const expected = crypto
    .createHmac('sha256', cfg.keySecret)
    .update(`${orderId}|${paymentId}`)
    .digest('hex');
  return (
    signature.length === expected.length &&
    crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(signature))
  );
}
