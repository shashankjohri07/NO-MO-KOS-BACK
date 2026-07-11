/**
 * Identity + admin gating for the /api/admin routes.
 *
 * The frontend talks to the auth service through a same-origin proxy
 * (/auth-api -> nomikos-auth-service), so the session cookie is scoped to the
 * frontend host and the browser also attaches it to same-origin /api/*
 * calls — which land here. We verify identity by forwarding that cookie to
 * the auth service's GET /auth/me (server-to-server) and reading the email.
 *
 * Admin = email present in the store's admin_roles table, OR listed in the
 * ADMIN_EMAILS env var (comma-separated bootstrap so the first admin doesn't
 * need DB access to grant themselves the role).
 */

import type { Request, Response, NextFunction } from 'express';
import type { Store } from './store';

const AUTH_SERVICE_URL = (
  process.env.AUTH_SERVICE_URL || 'https://nomikos-auth-service.onrender.com'
).replace(/\/+$/, '');

// Bootstrap admins: always-on regardless of DB state, so the owner can never
// lock themselves out. The first entry is the hardcoded owner; ADMIN_EMAILS
// (comma-separated, set in the Render dashboard) can add more. These are
// "protected" — the Manage-Admins UI shows them but cannot delete them
// (config, not data). Everyone else is granted via the admin_roles table.
const BOOTSTRAP_ADMINS = ['shashankjohri07@gmail.com'];

export const ENV_ADMIN_EMAILS = new Set(
  [
    ...BOOTSTRAP_ADMINS,
    ...(process.env.ADMIN_EMAILS || '').split(','),
  ]
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean),
);

const ADMIN_EMAILS = ENV_ADMIN_EMAILS;

export interface AuthedRequest extends Request {
  userEmail?: string;
  isAdmin?: boolean;
}

/** Resolve the caller's email by forwarding their cookie to /auth/me. */
export async function resolveEmail(req: Request): Promise<string | null> {
  const cookie = req.headers.cookie;
  if (!cookie) return null;
  try {
    const r = await fetch(`${AUTH_SERVICE_URL}/auth/me`, {
      headers: { cookie },
      signal: AbortSignal.timeout(10_000),
    });
    if (!r.ok) return null;
    const body: any = await r.json();
    const email = body?.data?.user?.email;
    return typeof email === 'string' && email.includes('@') ? email.toLowerCase() : null;
  } catch (e) {
    console.error(`[adminAuth] auth service check failed: ${e}`);
    return null;
  }
}

export function makeRequireAdmin(store: Store) {
  return async function requireAdmin(req: AuthedRequest, res: Response, next: NextFunction) {
    const email = await resolveEmail(req);
    if (!email) {
      res.status(401).json({ ok: false, error: 'Not signed in' });
      return;
    }
    let admin = ADMIN_EMAILS.has(email);
    if (!admin) {
      try {
        admin = await store.isAdmin(email);
      } catch (e) {
        console.error(`[adminAuth] role lookup failed: ${e}`);
      }
    }
    if (!admin) {
      res.status(403).json({ ok: false, error: 'Not an admin' });
      return;
    }
    req.userEmail = email;
    req.isAdmin = true;
    next();
  };
}

/** Like requireAdmin but never rejects — used by /whoami so the frontend can
 * decide whether to show the admin UI at all. */
export function makeWhoami(store: Store) {
  return async function whoami(req: AuthedRequest, res: Response) {
    const email = await resolveEmail(req);
    if (!email) {
      res.json({ ok: true, signedIn: false, isAdmin: false });
      return;
    }
    let admin = ADMIN_EMAILS.has(email);
    if (!admin) {
      try {
        admin = await store.isAdmin(email);
      } catch {
        /* role lookup failure -> treated as non-admin */
      }
    }
    res.json({ ok: true, signedIn: true, email, isAdmin: admin });
  };
}
