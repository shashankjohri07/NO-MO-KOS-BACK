/**
 * Event announcement emails via Resend's REST API (plain fetch, no SDK).
 *
 * RESEND_API_KEY unset -> dry-run mode: nothing is sent, the call reports
 * dryRun:true with the would-be recipient count. This keeps the feature
 * deployable before the key exists and makes local testing safe — a test
 * can never accidentally blast real inboxes.
 *
 * EMAIL_FROM should be a verified sender on the Resend account, e.g.
 * "Nomikos <events@yourdomain.com>" (defaults to Resend's shared onboarding
 * sender, which works out of the box but is fine only for testing).
 */

import type { EventRecord } from './store';

const RESEND_API_KEY = process.env.RESEND_API_KEY || '';
const EMAIL_FROM = process.env.EMAIL_FROM || 'Nomikos <onboarding@resend.dev>';
const BATCH_SIZE = 100; // Resend's batch endpoint cap

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

export async function sendEventEmail(ev: EventRecord, recipients: string[]): Promise<SendResult> {
  const { subject, html } = renderEventEmail(ev);

  if (!RESEND_API_KEY) {
    console.warn(
      `[email] RESEND_API_KEY not set — DRY RUN. Would send "${subject}" to ${recipients.length} subscriber(s).`,
    );
    return { sent: recipients.length, failed: 0, dryRun: true };
  }

  let sent = 0;
  let failed = 0;
  for (let i = 0; i < recipients.length; i += BATCH_SIZE) {
    const chunk = recipients.slice(i, i + BATCH_SIZE);
    // One personal email per recipient (no exposed CC list); batched per API call.
    const payload = chunk.map((to) => ({ from: EMAIL_FROM, to: [to], subject, html }));
    try {
      const r = await fetch('https://api.resend.com/emails/batch', {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${RESEND_API_KEY}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(30_000),
      });
      if (r.ok) {
        sent += chunk.length;
      } else {
        failed += chunk.length;
        console.error(`[email] batch ${i / BATCH_SIZE} failed: ${r.status} ${(await r.text()).slice(0, 300)}`);
      }
    } catch (e) {
      failed += chunk.length;
      console.error(`[email] batch ${i / BATCH_SIZE} threw: ${e}`);
    }
  }
  return { sent, failed, dryRun: false };
}
