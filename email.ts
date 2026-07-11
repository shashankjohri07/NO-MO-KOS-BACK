/**
 * Event announcement emails. Provider is chosen from env, in priority order:
 *
 *  1. Gmail SMTP  — set GMAIL_USER + GMAIL_APP_PASSWORD (a Google "App
 *     Password", needs 2-Step Verification on the account). Sends through
 *     Google's own servers, so a plain @gmail.com sender is properly
 *     authenticated and can email ANY recipient with no domain to verify.
 *     ~500 emails/day on a normal Gmail account.
 *  2. Resend     — set RESEND_API_KEY (needs a verified domain to email
 *     arbitrary recipients; otherwise only the account owner's address).
 *  3. dry-run    — neither set: nothing is sent, the call reports dryRun:true.
 *     Safe to deploy before any provider exists.
 */

import nodemailer, { type Transporter } from 'nodemailer';
import type { EventRecord } from './store';

const ENV_GMAIL_USER = process.env.GMAIL_USER || '';
const ENV_GMAIL_APP_PASSWORD = (process.env.GMAIL_APP_PASSWORD || '').replace(/\s+/g, ''); // Google shows it with spaces
const RESEND_API_KEY = process.env.RESEND_API_KEY || '';
const RESEND_BATCH = 100;

/**
 * Admin-editable email settings, saved in the store's app_config under the
 * key 'email_config' and applied at boot / on every admin save. Env vars are
 * the fallback when a field is blank, so an unconfigured dashboard keeps
 * today's behaviour exactly.
 */
export interface EmailConfig {
  gmailUser: string;
  gmailAppPassword: string;
  fromName: string;
}

let _cfg: Partial<EmailConfig> = {};

export function applyEmailConfig(cfg: Partial<EmailConfig> | null): void {
  _cfg = cfg || {};
  _tx = null; // credentials may have changed — drop the pooled connection
}

function effective(): { user: string; pass: string; from: string } {
  const user = (_cfg.gmailUser || '').trim() || ENV_GMAIL_USER;
  const pass = (_cfg.gmailAppPassword || '').replace(/\s+/g, '') || ENV_GMAIL_APP_PASSWORD;
  const name = (_cfg.fromName || '').trim() || 'Nomikos';
  const from =
    user ? `${name} <${user}>` : process.env.EMAIL_FROM || `${name} <onboarding@resend.dev>`;
  return { user, pass, from };
}

/** Sender address currently in effect (for the admin UI). */
export function currentSender(): { from: string; user: string } {
  const { from, user } = effective();
  return { from, user };
}

export function emailMode(): 'gmail' | 'resend' | 'dry-run' {
  const { user, pass } = effective();
  if (user && pass) return 'gmail';
  if (RESEND_API_KEY) return 'resend';
  return 'dry-run';
}

// One pooled SMTP connection reused across all recipients of a blast.
let _tx: Transporter | null = null;
function gmailTransport(): Transporter {
  if (!_tx) {
    const { user, pass } = effective();
    _tx = nodemailer.createTransport({
      host: 'smtp.gmail.com',
      port: 587,
      secure: false,
      requireTLS: true,   // force STARTTLS — prevents plaintext auth fallback
      pool: true,
      maxConnections: 3,
      auth: { user, pass },
      tls: { rejectUnauthorized: true },
    });
  }
  return _tx;
}

export function renderEventEmail(ev: EventRecord): { subject: string; html: string } {
  const dateStr = ev.event_date
    ? new Date(ev.event_date + 'T00:00:00').toLocaleDateString('en-IN', {
        weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
      })
    : '';
  const esc = (s: string) =>
    s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const paragraphs = ev.description
    .split(/\n+/)
    .map((p) => `<p style="margin:0 0 12px;line-height:1.6;color:#333;">${esc(p)}</p>`)
    .join('');

  const html = `<!doctype html>
<html><body style="margin:0;padding:0;background:#f4f1ea;font-family:Georgia,'Times New Roman',serif;">
  <div style="max-width:560px;margin:0 auto;padding:32px 16px;">
    <div style="text-align:center;padding:18px 0 26px;">
      <span style="font-size:30px;font-weight:bold;color:#1a1a1a;letter-spacing:-0.5px;">Nomikos</span><span style="font-size:30px;font-weight:bold;color:#b8962e;">.</span>
    </div>
    <div style="background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e8e2d4;">
      ${ev.image_url ? `<img src="${ev.image_url}" alt="" width="560" style="display:block;width:100%;height:auto;" />` : ''}
      <div style="padding:28px 30px;">
        <h1 style="margin:0 0 6px;font-size:24px;color:#1a1a1a;">${esc(ev.title)}</h1>
        ${dateStr ? `<p style="margin:0 0 18px;font-size:14px;color:#b8962e;font-weight:bold;">${dateStr}</p>` : ''}
        ${paragraphs}
        ${ev.link_url ? `<div style="text-align:center;margin:26px 0 8px;">
          <a href="${ev.link_url}" style="display:inline-block;background:#1a1a1a;color:#ffffff;text-decoration:none;padding:12px 28px;border-radius:8px;font-size:15px;">View details</a>
        </div>` : ''}
      </div>
    </div>
    <p style="text-align:center;font-size:11px;color:#999;margin-top:22px;line-height:1.6;">
      You are receiving this because you have an account on Nomikos.<br/>
      Reply to this email to unsubscribe from event updates.
    </p>
  </div>
</body></html>`;

  return { subject: `${ev.title} — Nomikos`, html };
}

export interface SendResult {
  sent: number;
  failed: number;
  dryRun: boolean;
}

async function sendViaGmail(subject: string, html: string, recipients: string[]): Promise<SendResult> {
  const tx = gmailTransport();
  let sent = 0;
  let failed = 0;
  // One message per recipient (no exposed recipient list). The pooled
  // transport reuses connections, so this stays fast for modest lists.
  for (const to of recipients) {
    try {
      await tx.sendMail({ from: effective().from, to, subject, html });
      sent++;
    } catch (e: unknown) {
      failed++;
      const code = (e as { code?: string; responseCode?: number })?.code;
      const resp = (e as { response?: string })?.response;
      console.error(`[email] gmail send to ${to} failed: code=${code} resp=${resp} err=${e}`);
    }
  }
  return { sent, failed, dryRun: false };
}

async function sendViaResend(subject: string, html: string, recipients: string[]): Promise<SendResult> {
  let sent = 0;
  let failed = 0;
  for (let i = 0; i < recipients.length; i += RESEND_BATCH) {
    const chunk = recipients.slice(i, i + RESEND_BATCH);
    const payload = chunk.map((to) => ({ from: effective().from, to: [to], subject, html }));
    try {
      const r = await fetch('https://api.resend.com/emails/batch', {
        method: 'POST',
        headers: { Authorization: `Bearer ${RESEND_API_KEY}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(30_000),
      });
      if (r.ok) sent += chunk.length;
      else {
        failed += chunk.length;
        console.error(`[email] resend batch ${i / RESEND_BATCH} failed: ${r.status} ${(await r.text()).slice(0, 300)}`);
      }
    } catch (e) {
      failed += chunk.length;
      console.error(`[email] resend batch ${i / RESEND_BATCH} threw: ${e}`);
    }
  }
  return { sent, failed, dryRun: false };
}

export async function sendEventEmail(ev: EventRecord, recipients: string[]): Promise<SendResult> {
  const { subject, html } = renderEventEmail(ev);
  const mode = emailMode();

  if (mode === 'dry-run') {
    console.warn(
      `[email] no provider configured — DRY RUN. Would send "${subject}" to ${recipients.length} subscriber(s).`,
    );
    return { sent: recipients.length, failed: 0, dryRun: true };
  }
  if (recipients.length === 0) return { sent: 0, failed: 0, dryRun: false };

  return mode === 'gmail'
    ? sendViaGmail(subject, html, recipients)
    : sendViaResend(subject, html, recipients);
}
