# Admin panel + event emails — setup

Adds an admin workspace to Nomikos: a dashboard (user stats + charts), a
"manage admins" panel, and an event composer that emails every signed-in user.
Normal users see no change at all.

## How it fits together

- **Identity** comes from the existing auth service. Our backend verifies a
  request by forwarding its session cookie to `GET /auth/me` and reading the
  email. No auth-service code change needed.
- **Roles** are ours: an email is admin if it is in `ADMIN_EMAILS`/the
  hardcoded owner (protected) **or** in the `admin_roles` table (added via the
  UI). Only an existing admin can grant admin — nobody can self-promote
  (every `/api/admin/*` route is behind `requireAdmin`).
- **Customer list** = the `subscribers` table. The frontend calls
  `POST /api/subscribe` after every login/signup, so the list fills itself.
- **Emails** go through Resend. Without `RESEND_API_KEY` the app runs in
  **dry-run** (counts recipients, sends nothing) — safe to deploy first.
- **Data store**: Supabase in production; a JSON file fallback for local dev
  (ephemeral — do NOT rely on it in prod).

## 1. Environment variables (Render → backend service → Environment)

| Var | Required | Purpose |
|-----|----------|---------|
| `SUPABASE_URL` | yes (prod) | e.g. `https://abc.supabase.co` |
| `SUPABASE_SERVICE_KEY` | yes (prod) | Supabase **service_role** key (server-only, never ship to frontend) |
| `RESEND_API_KEY` | for real emails | from resend.com; omit to stay in dry-run |
| `EMAIL_FROM` | recommended | e.g. `Nomikos <events@yourdomain.com>` (verified Resend sender). Defaults to Resend's shared `onboarding@resend.dev` (testing only) |
| `AUTH_SERVICE_URL` | optional | defaults to `https://nomikos-auth-service.onrender.com` |
| `ADMIN_EMAILS` | optional | extra protected admins, comma-separated. `shashankjohri07@gmail.com` is already hardcoded as the owner |

## 2. Supabase tables (SQL editor → run once)

```sql
create table if not exists subscribers (
  email text primary key,
  created_at timestamptz not null default now()
);

create table if not exists admin_roles (
  email text primary key,
  created_at timestamptz not null default now()
);

create table if not exists events (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  description text not null,
  event_date text,
  image_url text,
  link_url text,
  created_by text,
  created_at timestamptz not null default now(),
  sent_at timestamptz,
  sent_count int not null default 0
);

-- Generic JSON config (product card tags etc., edited from the admin UI)
create table if not exists app_config (
  key text primary key,
  value jsonb not null
);

-- Billing: paid subscriptions (one row per purchase; access is decided by
-- status='active' AND expires_at > now(), so expiry needs no scheduler)
create table if not exists subscriptions (
  id uuid primary key default gen_random_uuid(),
  email text not null,
  plan_id text not null,
  status text not null default 'active',
  started_at timestamptz not null default now(),
  expires_at timestamptz not null,
  razorpay_order_id text,
  razorpay_payment_id text,
  created_at timestamptz not null default now()
);
create index if not exists subscriptions_email_idx on subscriptions (email, status, expires_at);

-- Billing: one row per billable document run (quota counting)
create table if not exists usage_events (
  id bigint generated always as identity primary key,
  email text not null,
  tool text not null,
  created_at timestamptz not null default now()
);
create index if not exists usage_events_email_idx on usage_events (email, created_at);

-- User profile: display name + small avatar (data-URL), set after first login
create table if not exists profiles (
  email text primary key,
  username text not null,
  avatar text,
  updated_at timestamptz not null default now()
);
```

The backend uses the **service_role** key over PostgREST, which bypasses Row
Level Security, so no RLS policies are required. Keep that key server-side only.

## 3. Resend (free tier = 100 emails/day, 3,000/month)

1. Sign up at https://resend.com and create an **API key**.
2. (Recommended) Add your domain and verify it, then set
   `EMAIL_FROM="Nomikos <events@yourdomain.com>"`. Without a verified domain
   you can still test using the default `onboarding@resend.dev` sender.
3. Put the key in `RESEND_API_KEY` on Render and redeploy.

The composer sends one personalised email per recipient, batched 100 at a time
(matches the Resend batch cap and the free-tier daily limit). For lists larger
than 100/day you'd schedule batches or upgrade the Resend plan.

## 4. Using it

- The owner (`shashankjohri07@gmail.com`) just logs in normally → the user
  menu shows **Admin Workspace** → `/admin`.
- Dashboard: total users, new today / this week, events, emails sent, plus two
  charts (new users/day, cumulative growth).
- **Admins** card: add any email as admin, or remove a non-protected one.
- **New event**: title + description (+ optional date, link, banner image) →
  *Create & email N users* (or *Save without sending*).
- Switch back to the app any time via **Document Tools →**.

> Stats are computed from `subscribers.created_at` (first time we saw a user),
> i.e. "since this feature went live" — that's the only user data we own; the
> auth service's user table is separate.
