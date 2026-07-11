/**
 * Signature background removal via the remove.bg API.
 *
 * Before signature images are handed to the Python stamping pipeline, we
 * strip their background so the ink floats cleanly over the document. The
 * API produces far better results than the local white-threshold chroma-key
 * (which stays in the Python layer as the fallback for when this is
 * unconfigured or fails).
 *
 * Fail-open by design: any error — missing key, quota exhausted, network —
 * returns the ORIGINAL path so a filing never fails because of a cosmetic
 * enhancement. The API key comes from the REMOVEBG_API_KEY env var or the
 * admin-saved app_config('removebg_config') value.
 */

import fs from 'fs';
import type { Store } from './store';

const API_URL = 'https://api.remove.bg/v1.0/removebg';

let _cachedKey: string | null | undefined; // undefined = not looked up yet

export async function getRemoveBgKey(store: Store): Promise<string | null> {
  if (_cachedKey !== undefined) return _cachedKey;
  let key = (process.env.REMOVEBG_API_KEY || '').trim();
  if (!key) {
    try {
      const cfg = (await store.getConfig('removebg_config')) as { apiKey?: string } | null;
      key = (cfg?.apiKey || '').trim();
    } catch {
      /* config table missing — treat as unconfigured */
    }
  }
  _cachedKey = key || null;
  return _cachedKey;
}

/** Invalidate the cached key after the admin saves a new one. */
export function resetRemoveBgKeyCache(): void {
  _cachedKey = undefined;
}

/**
 * Strip the background from the image at `path` using remove.bg. On success
 * writes a PNG next to the original and returns its path; on ANY failure
 * returns the original path unchanged (Python's chroma-key still applies).
 */
export async function removeBackground(store: Store, path: string): Promise<string> {
  const key = await getRemoveBgKey(store);
  if (!key) return path;

  try {
    const buf = await fs.promises.readFile(path);
    const form = new FormData();
    form.append('image_file', new Blob([buf]), 'signature.png');
    form.append('size', 'auto');
    form.append('format', 'png');

    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 20_000);
    let resp: Response;
    try {
      resp = await fetch(API_URL, {
        method: 'POST',
        headers: { 'X-Api-Key': key },
        body: form,
        signal: ctrl.signal,
      });
    } finally {
      clearTimeout(timer);
    }

    if (!resp.ok) {
      const text = await resp.text();
      console.warn(`[removebg] ${resp.status} for ${path}: ${text.slice(0, 200)}`);
      return path;
    }
    const out = `${path}_nobg.png`;
    await fs.promises.writeFile(out, Buffer.from(await resp.arrayBuffer()));
    console.log(`[removebg] background removed: ${path} -> ${out}`);
    return out;
  } catch (e) {
    console.warn(`[removebg] failed for ${path} (${e}); using original`);
    return path;
  }
}
