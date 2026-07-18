import 'dotenv/config';
import express, { Request, Response } from 'express';
import cors from 'cors';
import multer from 'multer';
import { spawn } from 'child_process';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import fs from 'fs';

import { makeStore } from './store';
import { makeRequireAdmin, makeWhoami, resolveEmail, ENV_ADMIN_EMAILS, type AuthedRequest } from './adminAuth';
import {
  loadBillingConfig, getEntitlement, createOrder, verifySignature,
  ALL_TOOLS, type BillingPlan,
} from './billing';
import { sendEventEmail, emailMode, renderEventEmail, applyEmailConfig, currentSender, type EmailConfig } from './email';
import { removeBackground } from './removebg';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const UPLOAD_DIR = '/tmp/nomikos-uploads';
fs.mkdirSync(UPLOAD_DIR, { recursive: true });

const app = express();
const PORT = process.env.PORT ?? 3001;

// Per-file upload cap. Matches the nginx `client_max_body_size 500m`
// fronting the static / proxy layer — going higher here is pointless because
// nginx would reject first. Disk storage is used (see `dest`) so a 500MB
// upload does not consume 500MB of process memory.
const upload = multer({
  dest: UPLOAD_DIR,
  limits: { fileSize: 500 * 1024 * 1024 },
});

app.use(cors({ origin: process.env.FRONTEND_URL || '*' }));
app.use(express.json({ limit: '200mb' }));

app.get('/api/health', (_req: Request, res: Response) => {
  res.json({ status: 'ok', timestamp: new Date().toISOString() });
});

// ── Admin / events / subscribers ────────────────────────────────────────────
// Data lives in the store (Supabase in prod, JSON-file fallback in dev);
// identity is verified by forwarding the session cookie to the auth service;
// event emails go out through Resend (dry-run until RESEND_API_KEY is set).
const store = makeStore();
const requireAdmin = makeRequireAdmin(store);

// Apply the admin-saved email settings on boot (best-effort; env vars remain
// the fallback for any blank field, so a missing row keeps today's behaviour).
store
  .getConfig('email_config')
  .then((cfg) => {
    if (cfg && typeof cfg === 'object') applyEmailConfig(cfg as Partial<EmailConfig>);
    console.log(`[admin] store=${store.kind}, email=${emailMode()}`);
  })
  .catch(() => console.log(`[admin] store=${store.kind}, email=${emailMode()}`));

// Called by the frontend after a successful login/signup so the customer
// list fills itself — no auth-service DB access needed. Idempotent upsert.
app.post('/api/subscribe', async (req: Request, res: Response) => {
  const email = String(req.body?.email ?? '').trim().toLowerCase();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    res.status(400).json({ ok: false, error: 'Invalid email' });
    return;
  }
  try {
    await store.addSubscriber(email);
    res.json({ ok: true });
  } catch (e) {
    console.error(`[subscribe] ${e}`);
    res.status(500).json({ ok: false, error: 'Could not save subscription' });
  }
});

// The frontend asks this to decide whether to show the admin UI.
app.get('/api/admin/whoami', makeWhoami(store));

app.get('/api/admin/events', requireAdmin, async (_req: AuthedRequest, res: Response) => {
  try {
    res.json({ ok: true, events: await store.listEvents() });
  } catch (e) {
    console.error(`[admin/events] list: ${e}`);
    res.status(500).json({ ok: false, error: 'Could not load events' });
  }
});

app.get('/api/admin/subscribers', requireAdmin, async (_req: AuthedRequest, res: Response) => {
  try {
    const subs = await store.listSubscribers();
    res.json({ ok: true, count: subs.length, subscribers: subs });
  } catch (e) {
    console.error(`[admin/subscribers] ${e}`);
    res.status(500).json({ ok: false, error: 'Could not load subscribers' });
  }
});

// Dashboard stats — derived from the subscriber list (created_at = first time
// we saw a user) and the event log. All counts are "since the events feature
// went live", since that's the only user data we own (the auth service's DB
// is separate).
app.get('/api/admin/stats', requireAdmin, async (_req: AuthedRequest, res: Response) => {
  try {
    const [subs, events] = await Promise.all([store.listSubscribers(), store.listEvents()]);
    const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    const now = new Date();
    const todayStart = startOfDay(now);
    const weekStart = todayStart - 6 * 86400_000;

    let today = 0;
    let week = 0;
    // New users per day for the last 14 days (oldest -> newest) for the chart.
    const DAYS = 14;
    const buckets: { date: string; count: number }[] = [];
    const idx: Record<string, number> = {};
    for (let i = DAYS - 1; i >= 0; i--) {
      const d = new Date(todayStart - i * 86400_000);
      const key = d.toISOString().slice(0, 10);
      idx[key] = buckets.length;
      buckets.push({ date: key, count: 0 });
    }
    for (const s of subs) {
      const t = new Date(s.created_at).getTime();
      if (t >= todayStart) today++;
      if (t >= weekStart) week++;
      const key = new Date(s.created_at).toISOString().slice(0, 10);
      if (key in idx) buckets[idx[key]].count++;
    }

    const emailsSent = events.reduce((a, e) => a + (e.sent_count || 0), 0);

    res.json({
      ok: true,
      stats: {
        totalUsers: subs.length,
        newToday: today,
        newThisWeek: week,
        totalEvents: events.length,
        emailsSent,
        perDay: buckets,
      },
    });
  } catch (e) {
    console.error(`[admin/stats] ${e}`);
    res.status(500).json({ ok: false, error: 'Could not load stats' });
  }
});

// ── Manage admins ──
// Returns DB-granted admins (removable) plus the env/bootstrap admins
// (protected — config, not data, so they cannot be removed from the UI and
// nobody can lock the owner out).
app.get('/api/admin/admins', requireAdmin, async (_req: AuthedRequest, res: Response) => {
  try {
    const dbAdmins = await store.listAdmins();
    const env = [...ENV_ADMIN_EMAILS];
    const removable = dbAdmins.filter((e) => !ENV_ADMIN_EMAILS.has(e));
    res.json({
      ok: true,
      admins: [
        ...env.map((email) => ({ email, protected: true })),
        ...removable.map((email) => ({ email, protected: false })),
      ],
    });
  } catch (e) {
    console.error(`[admin/admins] list: ${e}`);
    res.status(500).json({ ok: false, error: 'Could not load admins' });
  }
});

// Promote any email to admin. requireAdmin already guarantees the caller is an
// admin, so nobody can self-promote — only an existing admin can grant.
app.post('/api/admin/admins', requireAdmin, async (req: AuthedRequest, res: Response) => {
  const email = String(req.body?.email ?? '').trim().toLowerCase();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    res.status(400).json({ ok: false, error: 'Invalid email' });
    return;
  }
  try {
    if (ENV_ADMIN_EMAILS.has(email)) {
      res.json({ ok: true, alreadyProtected: true }); // already a bootstrap admin
      return;
    }
    await store.addAdmin(email);
    console.log(`[admin/admins] ${req.userEmail} granted admin to ${email}`);
    res.json({ ok: true });
  } catch (e) {
    console.error(`[admin/admins] add: ${e}`);
    res.status(500).json({ ok: false, error: 'Could not add admin' });
  }
});

// Revoke a DB-granted admin. Protected (env/bootstrap) admins and the caller
// themselves cannot be removed — prevents lockout and demoting the owner.
app.delete('/api/admin/admins', requireAdmin, async (req: AuthedRequest, res: Response) => {
  const email = String(req.body?.email ?? '').trim().toLowerCase();
  if (ENV_ADMIN_EMAILS.has(email)) {
    res.status(400).json({ ok: false, error: 'This admin is protected (configured in env) and cannot be removed here.' });
    return;
  }
  if (email === req.userEmail) {
    res.status(400).json({ ok: false, error: 'You cannot remove your own admin access.' });
    return;
  }
  try {
    await store.removeAdmin(email);
    console.log(`[admin/admins] ${req.userEmail} revoked admin from ${email}`);
    res.json({ ok: true });
  } catch (e) {
    console.error(`[admin/admins] remove: ${e}`);
    res.status(500).json({ ok: false, error: 'Could not remove admin' });
  }
});

// Create an event; when sendNow is true, blast the announcement to every
// subscriber and record the sent count on the event.
app.post('/api/admin/events', requireAdmin, async (req: AuthedRequest, res: Response) => {
  const { title, description, event_date, image_url, link_url, sendNow } = req.body ?? {};
  if (!title || typeof title !== 'string' || !description || typeof description !== 'string') {
    res.status(400).json({ ok: false, error: 'title and description are required' });
    return;
  }
  try {
    const event = await store.createEvent({
      title: title.trim().slice(0, 200),
      description: String(description).trim().slice(0, 5000),
      event_date: typeof event_date === 'string' ? event_date.slice(0, 10) : '',
      image_url: typeof image_url === 'string' && image_url.trim() ? image_url.trim() : null,
      link_url: typeof link_url === 'string' && link_url.trim() ? link_url.trim() : null,
      created_by: req.userEmail || 'unknown',
    });

    let send = null;
    if (sendNow) {
      const subscribers = await store.listSubscribers();
      const result = await sendEventEmail(event, subscribers.map((s) => s.email));
      await store.markEventSent(event.id, result.sent);
      event.sent_at = new Date().toISOString();
      event.sent_count = result.sent;
      send = result;
      console.log(
        `[admin/events] "${event.title}" by ${req.userEmail}: sent=${result.sent} failed=${result.failed}${result.dryRun ? ' (DRY RUN)' : ''}`,
      );
    }
    res.json({ ok: true, event, send });
  } catch (e) {
    console.error(`[admin/events] create: ${e}`);
    res.status(500).json({ ok: false, error: 'Could not create event' });
  }
});

// Delete one past event (does not un-send any email, just removes the record).
app.delete('/api/admin/events/:id', requireAdmin, async (req: AuthedRequest, res: Response) => {
  const id = String(req.params.id || '').slice(0, 100);
  if (!id) { res.status(400).json({ ok: false, error: 'event id required' }); return; }
  try {
    await store.deleteEvent(id);
    console.log(`[admin/events] ${req.userEmail} deleted event ${id}`);
    res.json({ ok: true });
  } catch (e) {
    console.error(`[admin/events] delete: ${e}`);
    res.status(500).json({ ok: false, error: 'Could not delete event' });
  }
});

// Clear the whole event log in one click.
app.delete('/api/admin/events', requireAdmin, async (req: AuthedRequest, res: Response) => {
  try {
    await store.clearEvents();
    console.log(`[admin/events] ${req.userEmail} cleared ALL events`);
    res.json({ ok: true });
  } catch (e) {
    console.error(`[admin/events] clear: ${e}`);
    res.status(500).json({ ok: false, error: 'Could not clear events' });
  }
});

// Clear all user feedback entries.
app.delete('/api/admin/feedback', requireAdmin, async (req: AuthedRequest, res: Response) => {
  try {
    await store.clearFeedback();
    console.log(`[admin/feedback] ${req.userEmail} cleared ALL feedback`);
    res.json({ ok: true });
  } catch (e) {
    console.error(`[admin/feedback] clear: ${e}`);
    res.status(500).json({ ok: false, error: 'Could not clear feedback' });
  }
});

// ── Product tag config ──────────────────────────────────────────────────
// The landing/products page reads its card tags ("Live", "New", …) from
// here so an admin can change them without a code deploy. Public read;
// admin-only write. Shape: { [productKey]: { tag, tagVariant } }.
const TAG_VARIANTS = new Set(['live', 'soon', 'later']);

app.get('/api/products/config', async (_req: Request, res: Response) => {
  try {
    const tags = (await store.getConfig('product_tags')) ?? {};
    res.json({ ok: true, tags });
  } catch (e) {
    console.error(`[products/config] get: ${e}`);
    res.status(500).json({ ok: false, error: 'Could not load product config' });
  }
});

app.put('/api/admin/products/config', requireAdmin, async (req: AuthedRequest, res: Response) => {
  const raw = req.body?.tags;
  if (!raw || typeof raw !== 'object' || Array.isArray(raw) || Object.keys(raw).length > 50) {
    res.status(400).json({ ok: false, error: 'tags must be an object keyed by product' });
    return;
  }
  const tags: Record<string, { tag: string; tagVariant: string }> = {};
  for (const [key, v] of Object.entries(raw as Record<string, unknown>)) {
    const entry = v as { tag?: unknown; tagVariant?: unknown };
    const tag = String(entry?.tag ?? '').trim().slice(0, 30);
    const tagVariant = String(entry?.tagVariant ?? '').trim();
    if (!tag || !TAG_VARIANTS.has(tagVariant)) {
      res.status(400).json({ ok: false, error: `Invalid tag entry for "${key}"` });
      return;
    }
    tags[key.slice(0, 50)] = { tag, tagVariant };
  }
  try {
    await store.setConfig('product_tags', tags);
    console.log(`[products/config] updated by ${req.userEmail}: ${JSON.stringify(tags)}`);
    res.json({ ok: true, tags });
  } catch (e) {
    console.error(`[products/config] set: ${e}`);
    res.status(500).json({ ok: false, error: 'Could not save product config' });
  }
});

// ── Email settings (admin-editable) ─────────────────────────────────────
// Sender account + display name live in app_config('email_config') so the
// admin can rotate the Gmail account / app password without a redeploy.
// The app password is write-only: GET never returns it, only whether one
// is saved. Blank fields fall back to the GMAIL_USER / GMAIL_APP_PASSWORD
// env vars.
app.get('/api/admin/email/config', requireAdmin, async (_req: AuthedRequest, res: Response) => {
  try {
    const cfg = ((await store.getConfig('email_config')) ?? {}) as Partial<EmailConfig>;
    res.json({
      ok: true,
      config: {
        gmailUser: cfg.gmailUser || '',
        fromName: cfg.fromName || '',
        hasPassword: Boolean(cfg.gmailAppPassword),
      },
      effective: { ...currentSender(), mode: emailMode() },
    });
  } catch (e) {
    console.error(`[admin/email] get: ${e}`);
    res.status(500).json({ ok: false, error: 'Could not load email settings' });
  }
});

app.put('/api/admin/email/config', requireAdmin, async (req: AuthedRequest, res: Response) => {
  const gmailUser = String(req.body?.gmailUser ?? '').trim().toLowerCase().slice(0, 200);
  const fromName = String(req.body?.fromName ?? '').trim().slice(0, 100);
  // undefined = keep the stored password; '' = clear it; string = replace it.
  const rawPass = req.body?.gmailAppPassword;
  if (gmailUser && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(gmailUser)) {
    res.status(400).json({ ok: false, error: 'Sender email is not a valid address.' });
    return;
  }
  try {
    const prev = ((await store.getConfig('email_config')) ?? {}) as Partial<EmailConfig>;
    const gmailAppPassword =
      rawPass === undefined
        ? prev.gmailAppPassword || ''
        : String(rawPass).replace(/\s+/g, '').slice(0, 100);
    const cfg: EmailConfig = { gmailUser, fromName, gmailAppPassword };
    await store.setConfig('email_config', cfg);
    applyEmailConfig(cfg);
    console.log(`[admin/email] updated by ${req.userEmail}: user=${gmailUser || '(env)'} name=${fromName || '(default)'} pass=${gmailAppPassword ? 'set' : '(env)'}`);
    res.json({
      ok: true,
      config: { gmailUser, fromName, hasPassword: Boolean(gmailAppPassword) },
      effective: { ...currentSender(), mode: emailMode() },
    });
  } catch (e) {
    console.error(`[admin/email] set: ${e}`);
    res.status(500).json({ ok: false, error: 'Could not save email settings' });
  }
});

// ── remove.bg (signature background removal) ─────────────────────────────
// Key is write-only via the admin dashboard; env REMOVEBG_API_KEY also works.

app.get('/api/admin/removebg', requireAdmin, async (_req: AuthedRequest, res: Response) => {
  try {
    const cfg = ((await store.getConfig('removebg_config')) ?? {}) as { apiKey?: string };
    res.json({
      ok: true,
      hasKey: Boolean((cfg.apiKey || '').trim() || (process.env.REMOVEBG_API_KEY || '').trim()),
    });
  } catch (e) {
    console.error(`[admin/removebg] get: ${e}`);
    res.status(500).json({ ok: false, error: 'Could not load remove.bg settings' });
  }
});

app.put('/api/admin/removebg', requireAdmin, async (req: AuthedRequest, res: Response) => {
  // '' clears the saved key (env fallback still applies); string replaces it.
  const apiKey = String(req.body?.apiKey ?? '').trim().slice(0, 100);
  try {
    await store.setConfig('removebg_config', { apiKey });
    const { resetRemoveBgKeyCache } = await import('./removebg');
    resetRemoveBgKeyCache();
    console.log(`[admin/removebg] updated by ${req.userEmail}: key=${apiKey ? 'set' : 'cleared'}`);
    res.json({
      ok: true,
      hasKey: Boolean(apiKey || (process.env.REMOVEBG_API_KEY || '').trim()),
    });
  } catch (e) {
    console.error(`[admin/removebg] set: ${e}`);
    res.status(500).json({ ok: false, error: 'Could not save remove.bg settings' });
  }
});

// ── Billing (Spotify-style subscriptions) ────────────────────────────────
// Plans/keys are admin-configured in app_config('billing_config'); users buy
// a period through Razorpay; entitlement is computed lazily so an expired
// subscription simply stops working — no scheduler.

/** requireUser: any signed-in user (not necessarily an admin). */
async function requireUser(req: AuthedRequest, res: Response): Promise<string | null> {
  const email = await resolveEmail(req);
  if (!email) res.status(401).json({ ok: false, error: 'Please sign in first.' });
  else req.userEmail = email;
  return email;
}

// ── User profile (display name + avatar) ─────────────────────────────────
// Captured once after first login; avatar is a small client-resized data URL.

app.get('/api/profile', async (req: AuthedRequest, res: Response) => {
  const email = await requireUser(req, res);
  if (!email) return;
  try {
    const profile = await store.getProfile(email);
    res.json({ ok: true, profile });
  } catch (e) {
    console.error(`[profile] ${e}`);
    res.status(500).json({ ok: false, error: 'Could not load your profile' });
  }
});

app.put('/api/profile', async (req: AuthedRequest, res: Response) => {
  const email = await requireUser(req, res);
  if (!email) return;
  const { username, avatar } = req.body ?? {};
  const name = typeof username === 'string' ? username.trim() : '';
  if (!name || name.length > 50) {
    res.status(400).json({ ok: false, error: 'Username must be 1–50 characters.' });
    return;
  }
  let pic: string | null = null;
  if (typeof avatar === 'string' && avatar) {
    if (!avatar.startsWith('data:image/') || avatar.length > 300_000) {
      res.status(400).json({ ok: false, error: 'Profile picture must be a small image.' });
      return;
    }
    pic = avatar;
  }
  try {
    const profile = await store.saveProfile({ email, username: name, avatar: pic });
    res.json({ ok: true, profile });
  } catch (e) {
    console.error(`[profile:save] ${e}`);
    res.status(500).json({ ok: false, error: 'Could not save your profile' });
  }
});

// Public: plans + whether billing is on (no secrets).
app.get('/api/billing/plans', async (_req: Request, res: Response) => {
  try {
    const cfg = await loadBillingConfig(store);
    res.json({ ok: true, enabled: cfg.enabled, keyId: cfg.keyId, plans: cfg.plans });
  } catch (e) {
    console.error(`[billing/plans] ${e}`);
    res.status(500).json({ ok: false, error: 'Could not load plans' });
  }
});

// The signed-in user's current plan, quota and expiry.
app.get('/api/billing/me', async (req: AuthedRequest, res: Response) => {
  const email = await requireUser(req, res);
  if (!email) return;
  try {
    res.json({ ok: true, entitlement: await getEntitlement(store, email) });
  } catch (e) {
    console.error(`[billing/me] ${e}`);
    res.status(500).json({ ok: false, error: 'Could not load your plan' });
  }
});

// Start a purchase: create a Razorpay order for the chosen plan.
app.post('/api/billing/order', async (req: AuthedRequest, res: Response) => {
  const email = await requireUser(req, res);
  if (!email) return;
  try {
    const cfg = await loadBillingConfig(store);
    if (!cfg.enabled) { res.status(400).json({ ok: false, error: 'Billing is not enabled.' }); return; }
    if (!cfg.keyId || !cfg.keySecret) {
      res.status(503).json({ ok: false, error: 'Payments are not configured yet — contact the administrator.' });
      return;
    }
    const plan = cfg.plans.find((p) => p.id === String(req.body?.planId || ''));
    if (!plan || plan.priceInr <= 0) {
      res.status(400).json({ ok: false, error: 'Unknown or free plan.' });
      return;
    }
    const order = await createOrder(cfg, plan, email);
    res.json({ ok: true, ...order, keyId: cfg.keyId, plan: { id: plan.id, name: plan.name } });
  } catch (e) {
    console.error(`[billing/order] ${e}`);
    res.status(500).json({ ok: false, error: 'Could not start the payment. Try again.' });
  }
});

// Checkout success: verify the signature, then activate the subscription.
app.post('/api/billing/verify', async (req: AuthedRequest, res: Response) => {
  const email = await requireUser(req, res);
  if (!email) return;
  const { planId, orderId, paymentId, signature } = req.body ?? {};
  if (!planId || !orderId || !paymentId || !signature) {
    res.status(400).json({ ok: false, error: 'Missing payment details.' });
    return;
  }
  try {
    const cfg = await loadBillingConfig(store);
    const plan = cfg.plans.find((p) => p.id === String(planId));
    if (!plan) { res.status(400).json({ ok: false, error: 'Unknown plan.' }); return; }
    if (!verifySignature(cfg, String(orderId), String(paymentId), String(signature))) {
      console.error(`[billing/verify] BAD SIGNATURE for ${email} order=${orderId}`);
      res.status(400).json({ ok: false, error: 'Payment verification failed. If money was deducted it will be auto-refunded by Razorpay.' });
      return;
    }
    const now = new Date();
    const sub = await store.createSubscription({
      email,
      plan_id: plan.id,
      status: 'active',
      started_at: now.toISOString(),
      expires_at: new Date(now.getTime() + plan.periodDays * 86400_000).toISOString(),
      razorpay_order_id: String(orderId),
      razorpay_payment_id: String(paymentId),
    });
    console.log(`[billing] ${email} subscribed to ${plan.id} until ${sub.expires_at}`);
    res.json({ ok: true, entitlement: await getEntitlement(store, email) });
  } catch (e) {
    console.error(`[billing/verify] ${e}`);
    res.status(500).json({ ok: false, error: 'Could not activate the subscription — contact support with your payment id.' });
  }
});

// One billable document run: checks quota + tool access, records usage.
// Tools call this right before processing; a 402/403 tells the UI to show
// the upgrade prompt instead.
app.post('/api/billing/consume', async (req: AuthedRequest, res: Response) => {
  const email = await requireUser(req, res);
  if (!email) return;
  const tool = String(req.body?.tool ?? '').trim().slice(0, 50);
  try {
    const ent = await getEntitlement(store, email);
    if (!ent.billingEnabled) { res.json({ ok: true, remaining: -1 }); return; }
    if (tool && !ent.plan.tools.includes(tool)) {
      res.status(403).json({
        ok: false, code: 'tool_not_in_plan',
        error: `The ${tool} tool is not included in your ${ent.plan.name} plan.`,
      });
      return;
    }
    if (ent.remaining === 0) {
      res.status(402).json({
        ok: false, code: 'quota_exhausted',
        error: `You have used all ${ent.docsLimit} documents in your ${ent.plan.name} plan.`,
      });
      return;
    }
    await store.trackUsage(email, tool || 'unknown');
    res.json({ ok: true, remaining: ent.remaining === -1 ? -1 : ent.remaining - 1 });
  } catch (e) {
    console.error(`[billing/consume] ${e}`);
    // Fail-open: a billing hiccup must never block a paying user's filing.
    res.json({ ok: true, remaining: -1 });
  }
});

// Admin: read/write billing config (key secret is write-only, like the
// email password), list subscriptions.
app.get('/api/admin/billing/config', requireAdmin, async (_req: AuthedRequest, res: Response) => {
  try {
    const cfg = await loadBillingConfig(store);
    res.json({
      ok: true,
      config: {
        enabled: cfg.enabled,
        keyId: cfg.keyId,
        hasKeySecret: Boolean(cfg.keySecret),
        plans: cfg.plans,
      },
      allTools: ALL_TOOLS,
    });
  } catch (e) {
    console.error(`[admin/billing] get: ${e}`);
    res.status(500).json({ ok: false, error: 'Could not load billing config' });
  }
});

app.put('/api/admin/billing/config', requireAdmin, async (req: AuthedRequest, res: Response) => {
  try {
    const prev = await loadBillingConfig(store);
    const enabled = Boolean(req.body?.enabled);
    const keyId = String(req.body?.keyId ?? '').trim().slice(0, 100);
    const rawSecret = req.body?.keySecret; // undefined = keep, '' = clear
    const keySecret =
      rawSecret === undefined ? prev.keySecret : String(rawSecret).trim().slice(0, 200);

    const rawPlans = req.body?.plans;
    if (!Array.isArray(rawPlans) || rawPlans.length === 0 || rawPlans.length > 10) {
      res.status(400).json({ ok: false, error: 'Provide between 1 and 10 plans.' });
      return;
    }
    const plans: BillingPlan[] = [];
    const seen = new Set<string>();
    for (const p of rawPlans) {
      const id = String(p?.id ?? '').trim().toLowerCase().replace(/[^a-z0-9_-]/g, '').slice(0, 30);
      const name = String(p?.name ?? '').trim().slice(0, 40);
      const priceInr = Math.max(0, Math.floor(Number(p?.priceInr) || 0));
      const periodDays = Math.min(3660, Math.max(1, Math.floor(Number(p?.periodDays) || 30)));
      const docsRaw = Math.floor(Number(p?.docsPerPeriod));
      const docsPerPeriod = Number.isFinite(docsRaw) && docsRaw >= -1 ? docsRaw : 0;
      const tools = Array.isArray(p?.tools)
        ? p.tools.map((t: unknown) => String(t)).filter((t: string) => ALL_TOOLS.includes(t))
        : [];
      if (!id || !name || seen.has(id)) {
        res.status(400).json({ ok: false, error: `Every plan needs a unique id and a name (problem near "${name || id}").` });
        return;
      }
      seen.add(id);
      plans.push({
        id, name,
        description: String(p?.description ?? '').trim().slice(0, 200),
        priceInr, periodDays, docsPerPeriod, tools,
      });
    }
    if (!plans.some((p) => p.priceInr === 0)) {
      res.status(400).json({ ok: false, error: 'Keep at least one free (₹0) plan — it is what new users start on.' });
      return;
    }
    await store.setConfig('billing_config', { enabled, keyId, keySecret, plans });
    console.log(`[admin/billing] updated by ${req.userEmail}: enabled=${enabled} plans=${plans.map((p) => p.id).join(',')}`);
    res.json({ ok: true, config: { enabled, keyId, hasKeySecret: Boolean(keySecret), plans } });
  } catch (e) {
    console.error(`[admin/billing] set: ${e}`);
    res.status(500).json({ ok: false, error: 'Could not save billing config' });
  }
});

app.get('/api/admin/billing/subscriptions', requireAdmin, async (_req: AuthedRequest, res: Response) => {
  try {
    res.json({ ok: true, subscriptions: await store.listSubscriptions() });
  } catch (e) {
    res.status(500).json({ ok: false, error: 'Could not load subscriptions' });
  }
});

// Fire-and-forget tool usage tracking — no auth, ignores failures silently.
app.post('/api/track', async (req: Request, res: Response) => {
  const tool = String(req.body?.tool ?? '').trim().slice(0, 50);
  if (tool) store.trackTool(tool).catch(() => {});
  res.json({ ok: true });
});

// User feedback submission — no auth required.
app.post('/api/feedback', async (req: Request, res: Response) => {
  const message = String(req.body?.message ?? '').trim().slice(0, 2000);
  if (!message) { res.status(400).json({ ok: false, error: 'message required' }); return; }
  const email = typeof req.body?.email === 'string' ? req.body.email.trim().slice(0, 200) : null;
  const tool = typeof req.body?.tool === 'string' ? req.body.tool.trim().slice(0, 50) : null;
  try {
    await store.submitFeedback({ email: email || null, message, tool: tool || null });
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ ok: false, error: 'Could not save feedback' });
  }
});

// Admin: tool usage stats.
app.get('/api/admin/tool-stats', requireAdmin, async (_req: AuthedRequest, res: Response) => {
  try {
    const stats = await store.getToolStats();
    res.json({ ok: true, stats });
  } catch (e) {
    res.status(500).json({ ok: false, error: 'Could not load tool stats' });
  }
});

// Admin: list feedback entries.
app.get('/api/admin/feedback', requireAdmin, async (_req: AuthedRequest, res: Response) => {
  try {
    const entries = await store.listFeedback();
    res.json({ ok: true, entries });
  } catch (e) {
    res.status(500).json({ ok: false, error: 'Could not load feedback' });
  }
});

// Send a test email to the requesting admin only (no event saved, no blast).
app.post('/api/admin/events/test-send', requireAdmin, async (req: AuthedRequest, res: Response) => {
  const { title, description, event_date, image_url, link_url } = req.body ?? {};
  if (!title || typeof title !== 'string' || !description || typeof description !== 'string') {
    res.status(400).json({ ok: false, error: 'title and description are required' });
    return;
  }
  const ev = {
    id: 'test',
    title: String(title).trim().slice(0, 200),
    description: String(description).trim().slice(0, 5000),
    event_date: typeof event_date === 'string' ? event_date.slice(0, 10) : '',
    image_url: typeof image_url === 'string' && image_url.trim() ? image_url.trim() : null,
    link_url: typeof link_url === 'string' && link_url.trim() ? link_url.trim() : null,
    created_by: req.userEmail || 'test',
    created_at: new Date().toISOString(),
    sent_at: null,
    sent_count: 0,
  };
  try {
    const result = await sendEventEmail(ev, [req.userEmail!]);
    res.json({ ok: true, sent: result.sent, dryRun: result.dryRun });
  } catch (e) {
    console.error(`[admin/test-send] ${e}`);
    res.status(500).json({ ok: false, error: 'Could not send test email' });
  }
});

app.post('/api/detect-errors', upload.array('document', 5), async (req: Request, res: Response) => {
  const files = (req.files as Express.Multer.File[]) ?? [];
  if (files.length === 0) {
    res.status(400).json({ ok: false, error: 'No file uploaded' });
    return;
  }

  const filePaths = files.map((f) => f.path);
  const originalNames = files.map((f) => f.originalname).join(' + ');
  const totalMB = files.reduce((acc, f) => acc + f.size, 0) / 1024 / 1024;

  // 1-indexed last page of the index. Pages 1..N are skipped from the
  // pagination check. Defaults to 0 (= start checking from page 1).
  const rawIndexEnd = (req.body?.indexEndPage ?? '0') as string;
  const parsedIndexEnd = Number.parseInt(rawIndexEnd, 10);
  const indexEndPage = Number.isFinite(parsedIndexEnd) && parsedIndexEnd >= 0 ? parsedIndexEnd : 0;

  // mode: "detect" (rule check only) | "write" (stamp page numbers only —
  // skips extraction + rules for speed) | "both" (run everything).
  const rawMode = String(req.body?.mode ?? 'detect').toLowerCase();
  const mode: 'detect' | 'write' | 'both' =
    rawMode === 'write' || rawMode === 'both' ? rawMode : 'detect';

  console.log(
    `[detect-errors] Processing ${files.length} file(s): ${originalNames} (${totalMB.toFixed(1)}MB total) — indexEndPage=${indexEndPage} mode=${mode}`,
  );

  try {
    const args = [join(__dirname, 'server', 'error_detector.py')];
    for (const p of filePaths) {
      args.push('--file', p);
    }
    args.push('--index-end-page', String(indexEndPage));
    args.push('--mode', mode);

    const result = await new Promise<Record<string, unknown>>((resolve, reject) => {
      const proc = spawn('python3', args, {
        cwd: join(__dirname, 'server'),
        env: {
          ...process.env,
          // User PATH first (so pyenv/system python with installed deps wins),
          // brew/system bin paths appended for tesseract binary discovery.
          PATH: `${process.env.PATH ?? ''}:/opt/homebrew/bin:/usr/local/bin:/usr/bin`,
          PYTHONPATH: join(__dirname, 'server'),
        },
        timeout: 600_000,
      });

      let stdout = '';
      let stderr = '';

      proc.stdout.on('data', (data: Buffer) => {
        stdout += data.toString();
      });

      proc.stderr.on('data', (data: Buffer) => {
        stderr += data.toString();
        const line = data.toString().trim();
        if (line) console.log(`[detect-errors] ${line}`);
      });

      proc.on('close', (code: number | null) => {
        if (code === 0) {
          try {
            resolve(JSON.parse(stdout) as Record<string, unknown>);
          } catch {
            reject(new Error(`Invalid JSON from Python: ${stdout.substring(0, 500)}`));
          }
        } else {
          reject(new Error(`Python exited ${code}: ${stderr.substring(0, 500)}`));
        }
      });

      proc.on('error', (err: Error) => {
        reject(new Error(`Process error: ${err.message}`));
      });
    });

    for (const p of filePaths) fs.unlink(p, () => {});

    if (result && result.ok) {
      result.file = originalNames;
    }
    res.json(result);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error(`[detect-errors] Error:`, message);
    for (const p of filePaths) fs.unlink(p, () => {});
    res.status(500).json({ ok: false, error: message });
  }
});

app.post('/api/upload', upload.single('document'), (req: Request, res: Response) => {
  if (!req.file) {
    res.status(400).json({ success: false, error: 'No file uploaded' });
    return;
  }
  res.json({
    success: true,
    data: {
      filename: req.file.filename,
      originalName: req.file.originalname,
      mimetype: req.file.mimetype,
      size: req.file.size,
      path: req.file.path,
    },
  });
});

// Streaming page-numbering endpoint. Avoids the ~33% base64 inflation of the
// JSON path and never holds the full PDF in Node memory — Python writes
// bytes to stdout, we pipe straight into the HTTP response. Important on
// the Render free dyno (512MB RAM) and for large filings.
//
// Accepts TWO file fields in the same multipart payload:
//   - document: the main volumes (1..5), merged in upload order
//   - annex:    optional annexure files (each becomes one annexure: file 1
//               gets "Annexure A-1" stamped on its first page, file 2 gets
//               "Annexure A-2", and so on, then they are appended after the
//               main merged PDF and pagination continues across them).
// `maxCount` is set high (not removed) because multer requires a finite
// number — these caps exist only as a guardrail against accidental
// mass-uploads, not as a product limit. The per-file size cap (multer
// `limits.fileSize`) and nginx `client_max_body_size` still apply.
const uploadDualFields = upload.fields([
  { name: 'document', maxCount: 100 },
  { name: 'annex', maxCount: 100 },
  // Optional PNG/JPG signatures stamped in the footer of every annexure page.
  // Single file each — first matching field wins if duplicates posted.
  { name: 'clientSignature', maxCount: 1 },
  { name: 'clientSignature2', maxCount: 1 },
  { name: 'advocateSignature', maxCount: 1 },
  // Optional PNG/JPG signatures for the SPECIAL main-document pages listed in
  // signPages. Separate images from the annexure signatures above — these let
  // the user sign the vakalatnama / prayer page / affidavit with a distinct
  // signature set, independent of the every-annexure-page stamps.
  { name: 'specialSignatureClient', maxCount: 1 },
  { name: 'specialSignatureClient2', maxCount: 1 },
  { name: 'specialSignatureAdvocate', maxCount: 1 },
]);

// ── Single inline endpoint ────────────────────────────────────────────────
// Accepts a multipart payload (main PDFs, annexures, optional signatures,
// indexEndPage, optional signPages spec), spawns the Python pipeline, and
// streams the produced PDF directly back as the response body.
//
// No queue, no object storage, no polling — the web dyno does the work
// itself. Render's `client_max_body_size` and the multer `limits.fileSize`
// gate the upload size; Python timing is bounded by the 10-minute spawn
// timeout below.
app.post('/api/write-pagination', uploadDualFields, async (req: Request, res: Response) => {
  const fileMap = (req.files as Record<string, Express.Multer.File[]> | undefined) ?? {};
  const mainFiles = fileMap.document ?? [];
  const annexFiles = fileMap.annex ?? [];
  const clientSig = fileMap.clientSignature?.[0];
  const clientSig2 = fileMap.clientSignature2?.[0];
  const advocateSig = fileMap.advocateSignature?.[0];
  const specialClientSig = fileMap.specialSignatureClient?.[0];
  const specialClientSig2 = fileMap.specialSignatureClient2?.[0];
  const specialAdvocateSig = fileMap.specialSignatureAdvocate?.[0];
  if (mainFiles.length === 0) {
    res.status(400).json({ ok: false, error: 'No file uploaded' });
    return;
  }

  const mainPaths = mainFiles.map((f) => f.path);
  const annexPaths = annexFiles.map((f) => f.path);

  // Strip signature backgrounds via remove.bg (fail-open: original path on
  // any error; Python's chroma-key fallback still applies downstream).
  const clientSigPath = clientSig ? await removeBackground(store, clientSig.path) : undefined;
  const clientSig2Path = clientSig2 ? await removeBackground(store, clientSig2.path) : undefined;
  const advocateSigPath = advocateSig ? await removeBackground(store, advocateSig.path) : undefined;
  const specialClientSigPath = specialClientSig
    ? await removeBackground(store, specialClientSig.path) : undefined;
  const specialClientSig2Path = specialClientSig2
    ? await removeBackground(store, specialClientSig2.path) : undefined;
  const specialAdvocateSigPath = specialAdvocateSig
    ? await removeBackground(store, specialAdvocateSig.path) : undefined;

  const sigPaths = [
    clientSig?.path, clientSigPath,
    clientSig2?.path, clientSig2Path,
    advocateSig?.path, advocateSigPath,
    specialClientSig?.path, specialClientSigPath,
    specialClientSig2?.path, specialClientSig2Path,
    specialAdvocateSig?.path, specialAdvocateSigPath,
  ].filter((p): p is string => !!p);
  const cleanup = () => {
    for (const p of new Set([...mainPaths, ...annexPaths, ...sigPaths])) fs.unlink(p, () => {});
  };

  const rawIndexEnd = (req.body?.indexEndPage ?? '0') as string;
  const parsedIndexEnd = Number.parseInt(rawIndexEnd, 10);
  const indexEndPage = Number.isFinite(parsedIndexEnd) && parsedIndexEnd >= 0 ? parsedIndexEnd : 0;

  // Same lightweight cap on signPages as the async route. Python is the
  // source of truth for "is this a valid spec" — we just stop a 1MB
  // form-field bomb from being shoveled into argv.
  const rawSignPages = (req.body?.signPages ?? '') as string;
  const signPages =
    typeof rawSignPages === 'string' && rawSignPages.length <= 500
      ? rawSignPages.trim()
      : '';

  const mainNames = mainFiles.map((f) => f.originalname).join(' + ');
  const annexSummary = annexFiles.length ? ` + ${annexFiles.length} annex` : '';
  const sigSummary = [
    clientSig ? 'client-sig' : null,
    clientSig2 ? 'client2-sig' : null,
    advocateSig ? 'advocate-sig' : null,
    specialClientSig ? 'special-client-sig' : null,
    specialClientSig2 ? 'special-client2-sig' : null,
    specialAdvocateSig ? 'special-advocate-sig' : null,
  ]
    .filter(Boolean)
    .join('+');
  const totalMB =
    [
      ...mainFiles,
      ...annexFiles,
      ...(clientSig ? [clientSig] : []),
      ...(clientSig2 ? [clientSig2] : []),
      ...(advocateSig ? [advocateSig] : []),
      ...(specialClientSig ? [specialClientSig] : []),
      ...(specialClientSig2 ? [specialClientSig2] : []),
      ...(specialAdvocateSig ? [specialAdvocateSig] : []),
    ].reduce((acc, f) => acc + f.size, 0) / 1024 / 1024;
  console.log(
    `[write-pagination] ${mainFiles.length} main(s): ${mainNames}${annexSummary}${sigSummary ? ' + ' + sigSummary : ''} (${totalMB.toFixed(1)}MB) — indexEndPage=${indexEndPage}${signPages ? `, signPages='${signPages}'` : ''}`,
  );

  const args = [join(__dirname, 'server', 'error_detector.py')];
  for (const p of mainPaths) args.push('--file', p);
  for (const p of annexPaths) args.push('--annex', p);
  if (clientSigPath) args.push('--client-sig', clientSigPath);
  if (clientSig2Path) args.push('--client2-sig', clientSig2Path);
  if (advocateSigPath) args.push('--advocate-sig', advocateSigPath);
  if (specialClientSigPath) args.push('--special-sig-client', specialClientSigPath);
  if (specialClientSig2Path) args.push('--special-sig-client2', specialClientSig2Path);
  if (specialAdvocateSigPath) args.push('--special-sig-advocate', specialAdvocateSigPath);
  args.push('--index-end-page', String(indexEndPage));
  if (signPages) args.push('--sign-pages', signPages);
  args.push('--mode', 'write');
  args.push('--write-stdout');

  const proc = spawn('python3', args, {
    cwd: join(__dirname, 'server'),
    env: {
      ...process.env,
      PATH: `${process.env.PATH ?? ''}:/opt/homebrew/bin:/usr/local/bin:/usr/bin`,
      PYTHONPATH: join(__dirname, 'server'),
    },
    timeout: 600_000,
  });

  // Headers go out the moment the first byte arrives. We choose a sensible
  // download filename derived from the first input.
  const baseName = mainFiles[0].originalname.replace(/\.pdf$/i, '') || 'document';
  const downloadName = annexFiles.length
    ? `NUMBERED_WITH_ANNEXURES_${baseName}.pdf`
    : `NUMBERED_${baseName}.pdf`;
  res.setHeader('Content-Type', 'application/pdf');
  res.setHeader('Content-Disposition', `attachment; filename="${downloadName}"`);

  proc.stdout.pipe(res);

  let stderrBuf = '';
  proc.stderr.on('data', (d: Buffer) => {
    const line = d.toString().trim();
    if (line) console.log(`[write-pagination] ${line}`);
    stderrBuf += line + '\n';
  });

  proc.on('close', (code: number | null) => {
    cleanup();
    if (code !== 0 && !res.writableEnded) {
      // Stream might already be partially written — best we can do is end it.
      // Client will see a truncated/invalid PDF; logs carry the diagnosis.
      console.error(
        `[write-pagination] python exited ${code}: ${stderrBuf.substring(0, 500)}`,
      );
      if (!res.headersSent) {
        res.status(500).json({ ok: false, error: stderrBuf || `python exited ${code}` });
      } else {
        res.end();
      }
    }
  });

  proc.on('error', (err: Error) => {
    cleanup();
    if (!res.headersSent) {
      res.status(500).json({ ok: false, error: `Process error: ${err.message}` });
    } else {
      res.end();
    }
  });
});

// ── Bookmarks ────────────────────────────────────────────────────────────
// Stateless two-step flow, same spawn pattern as write-pagination:
//   detect  → upload PDF(s), get back a proposed heading tree as JSON. The
//             frontend renders it for review; nothing is stored server-side.
//   apply   → upload the SAME PDF(s) plus the user-finalized headings JSON,
//             stream back the PDF with the TOC injected.

app.post('/api/bookmarks/detect', upload.array('document', 5), (req: Request, res: Response) => {
  const files = (req.files as Express.Multer.File[]) ?? [];
  if (files.length === 0) {
    res.status(400).json({ ok: false, error: 'No file uploaded' });
    return;
  }
  const paths = files.map((f) => f.path);
  const cleanup = () => {
    for (const p of paths) fs.unlink(p, () => {});
  };

  console.log(
    `[bookmarks/detect] ${files.length} file(s): ${files.map((f) => f.originalname).join(' + ')}`,
  );

  const args = [join(__dirname, 'server', 'bookmarks.py'), 'detect'];
  for (const p of paths) args.push('--file', p);

  const proc = spawn('python3', args, {
    cwd: join(__dirname, 'server'),
    env: {
      ...process.env,
      PATH: `${process.env.PATH ?? ''}:/opt/homebrew/bin:/usr/local/bin:/usr/bin`,
      PYTHONPATH: join(__dirname, 'server'),
    },
    timeout: 300_000,
  });

  let stdoutBuf = '';
  let stderrBuf = '';
  proc.stdout.on('data', (d: Buffer) => (stdoutBuf += d.toString()));
  proc.stderr.on('data', (d: Buffer) => (stderrBuf += d.toString()));

  proc.on('close', (code: number | null) => {
    cleanup();
    if (code !== 0) {
      console.error(`[bookmarks/detect] python exited ${code}: ${stderrBuf.substring(0, 500)}`);
      res.status(500).json({ ok: false, error: 'Bookmark detection failed' });
      return;
    }
    try {
      res.json(JSON.parse(stdoutBuf));
    } catch {
      res.status(500).json({ ok: false, error: 'Invalid detection output' });
    }
  });

  proc.on('error', (err: Error) => {
    cleanup();
    res.status(500).json({ ok: false, error: `Process error: ${err.message}` });
  });
});

app.post('/api/bookmarks/apply', upload.array('document', 5), (req: Request, res: Response) => {
  const files = (req.files as Express.Multer.File[]) ?? [];
  if (files.length === 0) {
    res.status(400).json({ ok: false, error: 'No file uploaded' });
    return;
  }
  const paths = files.map((f) => f.path);

  // The finalized tree arrives as a JSON string form field. Parse to
  // validate + cap size, then hand it to Python via a temp file (argv has
  // length limits; a 1000-entry tree would blow past them).
  let headings: unknown;
  try {
    headings = JSON.parse(String(req.body?.headings ?? ''));
  } catch {
    headings = null;
  }
  if (!Array.isArray(headings) || headings.length === 0 || headings.length > 2000) {
    for (const p of paths) fs.unlink(p, () => {});
    res.status(400).json({ ok: false, error: 'headings must be a non-empty JSON array' });
    return;
  }
  const tocPath = join(UPLOAD_DIR, `toc-${Date.now()}-${Math.random().toString(36).slice(2)}.json`);
  fs.writeFileSync(tocPath, JSON.stringify(headings));

  const cleanup = () => {
    for (const p of [...paths, tocPath]) fs.unlink(p, () => {});
  };

  console.log(
    `[bookmarks/apply] ${files.length} file(s), ${headings.length} bookmark(s): ${files.map((f) => f.originalname).join(' + ')}`,
  );

  const args = [join(__dirname, 'server', 'bookmarks.py'), 'apply', '--toc-json', tocPath];
  for (const p of paths) args.push('--file', p);

  const proc = spawn('python3', args, {
    cwd: join(__dirname, 'server'),
    env: {
      ...process.env,
      PATH: `${process.env.PATH ?? ''}:/opt/homebrew/bin:/usr/local/bin:/usr/bin`,
      PYTHONPATH: join(__dirname, 'server'),
    },
    timeout: 600_000,
  });

  const baseName = files[0].originalname.replace(/\.pdf$/i, '') || 'document';
  res.setHeader('Content-Type', 'application/pdf');
  res.setHeader('Content-Disposition', `attachment; filename="BOOKMARKED_${baseName}.pdf"`);

  proc.stdout.pipe(res);

  let stderrBuf = '';
  proc.stderr.on('data', (d: Buffer) => {
    const line = d.toString().trim();
    if (line) console.log(`[bookmarks/apply] ${line}`);
    stderrBuf += line + '\n';
  });

  proc.on('close', (code: number | null) => {
    cleanup();
    if (code !== 0 && !res.writableEnded) {
      console.error(`[bookmarks/apply] python exited ${code}: ${stderrBuf.substring(0, 500)}`);
      if (!res.headersSent) {
        res.status(500).json({ ok: false, error: stderrBuf || `python exited ${code}` });
      } else {
        res.end();
      }
    }
  });

  proc.on('error', (err: Error) => {
    cleanup();
    if (!res.headersSent) {
      res.status(500).json({ ok: false, error: `Process error: ${err.message}` });
    } else {
      res.end();
    }
  });
});

// ── Index page generator ─────────────────────────────────────────────────
// Renders a court-filing "Master Index" page from the case details the user
// typed (court, case numbers, parties, rows, advocates, place/date). When
// document PDFs are attached, the index is prepended to the merged result;
// otherwise the index alone comes back. Payload travels as a JSON string
// form field and is handed to Python via a temp file (same reason as the
// bookmarks apply route — argv length limits).
app.post('/api/index/generate', upload.array('document', 5), (req: Request, res: Response) => {
  const files = (req.files as Express.Multer.File[]) ?? [];
  const paths = files.map((f) => f.path);

  let payload: unknown;
  try {
    payload = JSON.parse(String(req.body?.payload ?? ''));
  } catch {
    payload = null;
  }
  const rows = (payload as { rows?: unknown[] } | null)?.rows;
  if (!payload || typeof payload !== 'object' || !Array.isArray(rows) || rows.length === 0 || rows.length > 500) {
    for (const p of paths) fs.unlink(p, () => {});
    res.status(400).json({ ok: false, error: 'payload must include a non-empty rows array' });
    return;
  }
  const payloadPath = join(UPLOAD_DIR, `index-${Date.now()}-${Math.random().toString(36).slice(2)}.json`);
  fs.writeFileSync(payloadPath, JSON.stringify(payload));

  const cleanup = () => {
    for (const p of [...paths, payloadPath]) fs.unlink(p, () => {});
  };

  console.log(
    `[index/generate] ${rows.length} row(s)${files.length ? `, prepending to ${files.map((f) => f.originalname).join(' + ')}` : ' (index only)'}`,
  );

  const args = [join(__dirname, 'server', 'index_page.py'), 'generate', '--payload', payloadPath];
  for (const p of paths) args.push('--file', p);

  const proc = spawn('python3', args, {
    cwd: join(__dirname, 'server'),
    env: {
      ...process.env,
      PATH: `${process.env.PATH ?? ''}:/opt/homebrew/bin:/usr/local/bin:/usr/bin`,
      PYTHONPATH: join(__dirname, 'server'),
    },
    timeout: 600_000,
  });

  const baseName = files[0]?.originalname.replace(/\.pdf$/i, '') || 'index';
  const downloadName = files.length ? `INDEXED_${baseName}.pdf` : 'INDEX.pdf';
  res.setHeader('Content-Type', 'application/pdf');
  res.setHeader('Content-Disposition', `attachment; filename="${downloadName}"`);

  proc.stdout.pipe(res);

  let stderrBuf = '';
  proc.stderr.on('data', (d: Buffer) => {
    const line = d.toString().trim();
    if (line) console.log(`[index/generate] ${line}`);
    stderrBuf += line + '\n';
  });

  proc.on('close', (code: number | null) => {
    cleanup();
    if (code !== 0 && !res.writableEnded) {
      console.error(`[index/generate] python exited ${code}: ${stderrBuf.substring(0, 500)}`);
      if (!res.headersSent) {
        res.status(500).json({ ok: false, error: stderrBuf || `python exited ${code}` });
      } else {
        res.end();
      }
    }
  });

  proc.on('error', (err: Error) => {
    cleanup();
    if (!res.headersSent) {
      res.status(500).json({ ok: false, error: `Process error: ${err.message}` });
    } else {
      res.end();
    }
  });
});

app.listen(PORT, () => {
  console.log(`API server running on http://localhost:${PORT}`);
});
