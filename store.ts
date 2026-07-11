/**
 * Data layer for the admin/events feature: events, subscribers, admin roles.
 *
 * Two backends behind one interface:
 *  - SupabaseStore — production. Talks to Supabase's PostgREST API with plain
 *    fetch (no SDK dependency). Requires SUPABASE_URL + SUPABASE_SERVICE_KEY
 *    env vars and the tables created by the SQL in README-ADMIN.md.
 *  - FileStore — dev fallback when Supabase env is missing. A JSON file under
 *    /tmp; fine for local testing, EPHEMERAL on Render (lost on redeploy) —
 *    production must configure Supabase.
 */

import fs from 'fs';
import path from 'path';

export interface EventRecord {
  id: string;
  title: string;
  description: string;
  event_date: string; // ISO date
  image_url: string | null;
  link_url: string | null;
  created_by: string;
  created_at: string;
  sent_at: string | null;
  sent_count: number;
}

export interface Subscriber {
  email: string;
  created_at: string;
}

export interface ToolStat {
  tool: string;
  count: number;
}

export interface FeedbackEntry {
  id: string;
  email: string | null;
  message: string;
  tool: string | null;
  created_at: string;
}

export interface SubscriptionRecord {
  id: string;
  email: string;
  plan_id: string;
  status: 'active' | 'cancelled';
  started_at: string;
  expires_at: string;
  razorpay_order_id: string | null;
  razorpay_payment_id: string | null;
  created_at: string;
}

export interface Store {
  addSubscriber(email: string): Promise<void>;
  listSubscribers(): Promise<Subscriber[]>;
  isAdmin(email: string): Promise<boolean>;
  addAdmin(email: string): Promise<void>;
  removeAdmin(email: string): Promise<void>;
  listAdmins(): Promise<string[]>;
  createEvent(e: Omit<EventRecord, 'id' | 'created_at' | 'sent_at' | 'sent_count'>): Promise<EventRecord>;
  markEventSent(id: string, sentCount: number): Promise<void>;
  listEvents(): Promise<EventRecord[]>;
  deleteEvent(id: string): Promise<void>;
  clearEvents(): Promise<void>;
  clearFeedback(): Promise<void>;
  trackTool(tool: string): Promise<void>;
  getToolStats(): Promise<ToolStat[]>;
  submitFeedback(entry: { email?: string | null; message: string; tool?: string | null }): Promise<void>;
  listFeedback(): Promise<FeedbackEntry[]>;
  // Generic JSON key-value config (product tags, feature flags, …).
  getConfig(key: string): Promise<unknown | null>;
  setConfig(key: string, value: unknown): Promise<void>;
  // Billing: paid subscriptions + per-user document usage.
  createSubscription(
    s: Omit<SubscriptionRecord, 'id' | 'created_at'>,
  ): Promise<SubscriptionRecord>;
  /** Latest non-expired active subscription for the user, or null. */
  getActiveSubscription(email: string): Promise<SubscriptionRecord | null>;
  cancelSubscription(email: string): Promise<void>;
  listSubscriptions(): Promise<SubscriptionRecord[]>;
  /** Record one billable document run for the user. */
  trackUsage(email: string, tool: string): Promise<void>;
  /** How many document runs the user has made since the given ISO time. */
  countUsageSince(email: string, sinceIso: string): Promise<number>;
  kind: string;
}

// ── Supabase (PostgREST over fetch) ────────────────────────────────────────

class SupabaseStore implements Store {
  kind = 'supabase';
  constructor(private url: string, private key: string) {}

  private async req(pathQs: string, init: RequestInit = {}): Promise<any> {
    const r = await fetch(`${this.url}/rest/v1/${pathQs}`, {
      ...init,
      headers: {
        apikey: this.key,
        Authorization: `Bearer ${this.key}`,
        'Content-Type': 'application/json',
        Prefer: 'return=representation',
        ...(init.headers || {}),
      },
    });
    if (!r.ok) {
      const body = await r.text();
      throw new Error(`supabase ${init.method || 'GET'} ${pathQs}: ${r.status} ${body.slice(0, 200)}`);
    }
    const text = await r.text();
    return text ? JSON.parse(text) : null;
  }

  async addSubscriber(email: string): Promise<void> {
    // Upsert on the email PK — repeat logins are a no-op.
    await this.req('subscribers?on_conflict=email', {
      method: 'POST',
      headers: { Prefer: 'resolution=ignore-duplicates' },
      body: JSON.stringify({ email }),
    });
  }

  async listSubscribers(): Promise<Subscriber[]> {
    return (await this.req('subscribers?select=email,created_at&order=created_at.asc')) ?? [];
  }

  async isAdmin(email: string): Promise<boolean> {
    const rows = await this.req(`admin_roles?select=email&email=eq.${encodeURIComponent(email)}`);
    return Array.isArray(rows) && rows.length > 0;
  }

  async addAdmin(email: string): Promise<void> {
    await this.req('admin_roles?on_conflict=email', {
      method: 'POST',
      headers: { Prefer: 'resolution=ignore-duplicates' },
      body: JSON.stringify({ email }),
    });
  }

  async removeAdmin(email: string): Promise<void> {
    await this.req(`admin_roles?email=eq.${encodeURIComponent(email)}`, { method: 'DELETE' });
  }

  async listAdmins(): Promise<string[]> {
    const rows = await this.req('admin_roles?select=email&order=email.asc');
    return Array.isArray(rows) ? rows.map((r: any) => r.email) : [];
  }

  async createEvent(e: Omit<EventRecord, 'id' | 'created_at' | 'sent_at' | 'sent_count'>): Promise<EventRecord> {
    const rows = await this.req('events', { method: 'POST', body: JSON.stringify(e) });
    return rows[0];
  }

  async markEventSent(id: string, sentCount: number): Promise<void> {
    await this.req(`events?id=eq.${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify({ sent_at: new Date().toISOString(), sent_count: sentCount }),
    });
  }

  async listEvents(): Promise<EventRecord[]> {
    return (await this.req('events?select=*&order=created_at.desc&limit=100')) ?? [];
  }

  async deleteEvent(id: string): Promise<void> {
    await this.req(`events?id=eq.${encodeURIComponent(id)}`, { method: 'DELETE' });
  }

  async clearEvents(): Promise<void> {
    // PostgREST requires a filter on DELETE; "id not null" matches every row.
    await this.req('events?id=not.is.null', { method: 'DELETE' });
  }

  async clearFeedback(): Promise<void> {
    await this.req('feedback?id=not.is.null', { method: 'DELETE' });
  }

  async trackTool(tool: string): Promise<void> {
    try {
      await this.req('tool_events', {
        method: 'POST',
        headers: { Prefer: 'return=minimal' },
        body: JSON.stringify({ tool }),
      });
    } catch (e) {
      console.warn(`[store] trackTool failed (table may not exist yet): ${e}`);
    }
  }

  async getToolStats(): Promise<ToolStat[]> {
    try {
      const rows: { tool: string }[] = (await this.req('tool_events?select=tool')) ?? [];
      const counts: Record<string, number> = {};
      for (const r of rows) counts[r.tool] = (counts[r.tool] || 0) + 1;
      return Object.entries(counts)
        .map(([tool, count]) => ({ tool, count }))
        .sort((a, b) => b.count - a.count);
    } catch (e) {
      console.warn(`[store] getToolStats failed: ${e}`);
      return [];
    }
  }

  async submitFeedback(entry: { email?: string | null; message: string; tool?: string | null }): Promise<void> {
    try {
      await this.req('feedback', {
        method: 'POST',
        headers: { Prefer: 'return=minimal' },
        body: JSON.stringify(entry),
      });
    } catch (e) {
      console.warn(`[store] submitFeedback failed (table may not exist yet): ${e}`);
    }
  }

  async listFeedback(): Promise<FeedbackEntry[]> {
    try {
      return (await this.req('feedback?select=*&order=created_at.desc&limit=200')) ?? [];
    } catch (e) {
      console.warn(`[store] listFeedback failed: ${e}`);
      return [];
    }
  }

  // Table: app_config(key text primary key, value jsonb) — see README-ADMIN.md.
  async getConfig(key: string): Promise<unknown | null> {
    try {
      const rows = await this.req(`app_config?select=value&key=eq.${encodeURIComponent(key)}`);
      return Array.isArray(rows) && rows.length > 0 ? rows[0].value : null;
    } catch (e) {
      console.warn(`[store] getConfig(${key}) failed (table may not exist yet): ${e}`);
      return null;
    }
  }

  async setConfig(key: string, value: unknown): Promise<void> {
    await this.req('app_config?on_conflict=key', {
      method: 'POST',
      headers: { Prefer: 'resolution=merge-duplicates' },
      body: JSON.stringify({ key, value }),
    });
  }

  // Tables: subscriptions + usage_events — see README-ADMIN.md for the SQL.
  async createSubscription(
    s: Omit<SubscriptionRecord, 'id' | 'created_at'>,
  ): Promise<SubscriptionRecord> {
    const rows = await this.req('subscriptions', { method: 'POST', body: JSON.stringify(s) });
    return rows[0];
  }

  async getActiveSubscription(email: string): Promise<SubscriptionRecord | null> {
    const rows = await this.req(
      `subscriptions?email=eq.${encodeURIComponent(email)}&status=eq.active` +
        `&expires_at=gt.${encodeURIComponent(new Date().toISOString())}` +
        `&order=expires_at.desc&limit=1`,
    );
    return Array.isArray(rows) && rows.length > 0 ? rows[0] : null;
  }

  async cancelSubscription(email: string): Promise<void> {
    await this.req(
      `subscriptions?email=eq.${encodeURIComponent(email)}&status=eq.active`,
      { method: 'PATCH', body: JSON.stringify({ status: 'cancelled' }) },
    );
  }

  async listSubscriptions(): Promise<SubscriptionRecord[]> {
    try {
      return (await this.req('subscriptions?select=*&order=created_at.desc&limit=500')) ?? [];
    } catch (e) {
      console.warn(`[store] listSubscriptions failed (table may not exist yet): ${e}`);
      return [];
    }
  }

  async trackUsage(email: string, tool: string): Promise<void> {
    await this.req('usage_events', {
      method: 'POST',
      headers: { Prefer: 'return=minimal' },
      body: JSON.stringify({ email, tool }),
    });
  }

  async countUsageSince(email: string, sinceIso: string): Promise<number> {
    const r = await fetch(
      `${this.url}/rest/v1/usage_events?email=eq.${encodeURIComponent(email)}` +
        `&created_at=gte.${encodeURIComponent(sinceIso)}&select=id`,
      {
        headers: {
          apikey: this.key,
          Authorization: `Bearer ${this.key}`,
          Prefer: 'count=exact',
          Range: '0-0',
        },
      },
    );
    // Content-Range: "0-0/NNN" — the total after the slash is the count.
    const total = (r.headers.get('content-range') || '').split('/')[1];
    return total && total !== '*' ? parseInt(total, 10) : 0;
  }
}

// ── JSON-file fallback (dev / unconfigured) ────────────────────────────────

interface FileShape {
  subscribers: Subscriber[];
  events: EventRecord[];
  admin_roles: string[];
  tool_events: { tool: string; created_at: string }[];
  feedback: FeedbackEntry[];
  app_config: Record<string, unknown>;
  subscriptions: SubscriptionRecord[];
  usage_events: { email: string; tool: string; created_at: string }[];
}

class FileStore implements Store {
  kind = 'file';
  constructor(private file: string) {}

  private read(): FileShape {
    try {
      const d = JSON.parse(fs.readFileSync(this.file, 'utf8'));
      d.tool_events ??= [];
      d.feedback ??= [];
      d.app_config ??= {};
      d.subscriptions ??= [];
      d.usage_events ??= [];
      return d;
    } catch {
      return {
        subscribers: [], events: [], admin_roles: [], tool_events: [],
        feedback: [], app_config: {}, subscriptions: [], usage_events: [],
      };
    }
  }

  private write(d: FileShape): void {
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    fs.writeFileSync(this.file, JSON.stringify(d, null, 2));
  }

  async addSubscriber(email: string): Promise<void> {
    const d = this.read();
    if (!d.subscribers.some((s) => s.email === email)) {
      d.subscribers.push({ email, created_at: new Date().toISOString() });
      this.write(d);
    }
  }

  async listSubscribers(): Promise<Subscriber[]> {
    return this.read().subscribers;
  }

  async isAdmin(email: string): Promise<boolean> {
    return this.read().admin_roles.includes(email);
  }

  async addAdmin(email: string): Promise<void> {
    const d = this.read();
    if (!d.admin_roles.includes(email)) {
      d.admin_roles.push(email);
      this.write(d);
    }
  }

  async removeAdmin(email: string): Promise<void> {
    const d = this.read();
    d.admin_roles = d.admin_roles.filter((e) => e !== email);
    this.write(d);
  }

  async listAdmins(): Promise<string[]> {
    return this.read().admin_roles;
  }

  async createEvent(e: Omit<EventRecord, 'id' | 'created_at' | 'sent_at' | 'sent_count'>): Promise<EventRecord> {
    const d = this.read();
    const rec: EventRecord = {
      ...e,
      id: `evt_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
      created_at: new Date().toISOString(),
      sent_at: null,
      sent_count: 0,
    };
    d.events.unshift(rec);
    this.write(d);
    return rec;
  }

  async markEventSent(id: string, sentCount: number): Promise<void> {
    const d = this.read();
    const ev = d.events.find((x) => x.id === id);
    if (ev) {
      ev.sent_at = new Date().toISOString();
      ev.sent_count = sentCount;
      this.write(d);
    }
  }

  async listEvents(): Promise<EventRecord[]> {
    return this.read().events;
  }

  async deleteEvent(id: string): Promise<void> {
    const d = this.read();
    d.events = d.events.filter((e) => e.id !== id);
    this.write(d);
  }

  async clearEvents(): Promise<void> {
    const d = this.read();
    d.events = [];
    this.write(d);
  }

  async clearFeedback(): Promise<void> {
    const d = this.read();
    d.feedback = [];
    this.write(d);
  }

  async trackTool(tool: string): Promise<void> {
    const d = this.read();
    d.tool_events.push({ tool, created_at: new Date().toISOString() });
    this.write(d);
  }

  async getToolStats(): Promise<ToolStat[]> {
    const counts: Record<string, number> = {};
    for (const e of this.read().tool_events) counts[e.tool] = (counts[e.tool] || 0) + 1;
    return Object.entries(counts)
      .map(([tool, count]) => ({ tool, count }))
      .sort((a, b) => b.count - a.count);
  }

  async submitFeedback(entry: { email?: string | null; message: string; tool?: string | null }): Promise<void> {
    const d = this.read();
    d.feedback.push({
      id: `fb_${Date.now()}`,
      email: entry.email ?? null,
      message: entry.message,
      tool: entry.tool ?? null,
      created_at: new Date().toISOString(),
    });
    this.write(d);
  }

  async listFeedback(): Promise<FeedbackEntry[]> {
    return [...this.read().feedback].reverse();
  }

  async getConfig(key: string): Promise<unknown | null> {
    return this.read().app_config[key] ?? null;
  }

  async setConfig(key: string, value: unknown): Promise<void> {
    const d = this.read();
    d.app_config[key] = value;
    this.write(d);
  }

  async createSubscription(
    s: Omit<SubscriptionRecord, 'id' | 'created_at'>,
  ): Promise<SubscriptionRecord> {
    const d = this.read();
    const rec: SubscriptionRecord = {
      ...s,
      id: `sub_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
      created_at: new Date().toISOString(),
    };
    d.subscriptions.unshift(rec);
    this.write(d);
    return rec;
  }

  async getActiveSubscription(email: string): Promise<SubscriptionRecord | null> {
    const now = new Date().toISOString();
    return (
      this.read().subscriptions.find(
        (s) => s.email === email && s.status === 'active' && s.expires_at > now,
      ) ?? null
    );
  }

  async cancelSubscription(email: string): Promise<void> {
    const d = this.read();
    for (const s of d.subscriptions) {
      if (s.email === email && s.status === 'active') s.status = 'cancelled';
    }
    this.write(d);
  }

  async listSubscriptions(): Promise<SubscriptionRecord[]> {
    return this.read().subscriptions;
  }

  async trackUsage(email: string, tool: string): Promise<void> {
    const d = this.read();
    d.usage_events.push({ email, tool, created_at: new Date().toISOString() });
    this.write(d);
  }

  async countUsageSince(email: string, sinceIso: string): Promise<number> {
    return this.read().usage_events.filter((u) => u.email === email && u.created_at >= sinceIso)
      .length;
  }
}

export function makeStore(): Store {
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_KEY;
  if (url && key) return new SupabaseStore(url.replace(/\/+$/, ''), key);
  const file = process.env.DATA_FILE || '/tmp/nomikos-admin-data.json';
  console.warn(
    `[store] SUPABASE_URL/SUPABASE_SERVICE_KEY not set — using ephemeral file store at ${file}. ` +
      'Configure Supabase before relying on this in production.',
  );
  return new FileStore(file);
}
