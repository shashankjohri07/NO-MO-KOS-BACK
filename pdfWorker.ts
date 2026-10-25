/**
 * Drop-in replacement for `spawn('python3', [scriptPath, ...args], opts)`
 * for the PDF pipeline scripts (error_detector.py / bookmarks.py),
 * backed by the persistent worker (server/worker.py) instead of a fresh
 * subprocess per call.
 *
 * Why: the old code spawned a brand-new python3 process for every upload,
 * paying PyMuPDF/PIL/pytesseract's import cost (and tesseract binary
 * discovery) every single time — the dominant cost on a free-tier
 * single-core dyno. The worker imports everything once at boot and stays
 * warm; this module just gives every existing call site the exact same
 * object shape a ChildProcess has (`.stdout`, `.stderr`, `.on('close', ...)`,
 * `.on('error', ...)`), so NONE of the five route handlers in server.ts had
 * to change their streaming / cleanup / error-handling logic — only the
 * `spawn('python3', args, opts)` call itself is replaced with
 * `runPythonJob(args, opts)`.
 *
 * Safety net: if the worker process isn't up yet (still starting, or it
 * crashed and is restarting), this transparently falls back to the
 * original per-request `spawn('python3', ...)` path. Worst case behavior
 * is therefore never worse than before this change — only potentially
 * faster.
 *
 * Concurrency: the worker (http.server.HTTPServer, no threading) handles
 * one job at a time by design — see worker.py's module docstring for why.
 * `MAX_QUEUE` below caps how many callers may be waiting for that single
 * slot before this starts rejecting fast with a clear error instead of
 * piling up queued PDF uploads in memory under real overload.
 */
import { spawn, type ChildProcess, type ChildProcessWithoutNullStreams } from 'child_process';
import { EventEmitter } from 'events';
import { PassThrough, Readable } from 'stream';
import { basename, join } from 'path';

const WORKER_PORT = Number(process.env.NOMIKOS_WORKER_PORT ?? 8991);
const WORKER_URL = `http://127.0.0.1:${WORKER_PORT}`;
const MAX_CONCURRENT = 1; // matches the worker's single-threaded accept loop
const MAX_QUEUE = 8; // callers waiting beyond this get a fast "busy" error

let workerProc: ChildProcess | null = null;
let workerReady = false;
let restartTimer: NodeJS.Timeout | null = null;

/** Spawns the persistent worker once at server boot. Safe to call multiple
 * times (no-ops if already running); auto-restarts on crash. */
export function startPdfWorker(serverDir: string): void {
  if (workerProc) return;

  const proc = spawn('python3', [join(serverDir, 'worker.py')], {
    cwd: serverDir,
    env: {
      ...process.env,
      PATH: `${process.env.PATH ?? ''}:/opt/homebrew/bin:/usr/local/bin:/usr/bin`,
      PYTHONPATH: serverDir,
      NOMIKOS_WORKER_PORT: String(WORKER_PORT),
    },
    stdio: ['ignore', 'ignore', 'pipe'],
  });
  workerProc = proc;
  workerReady = false;

  proc.stderr?.on('data', (d: Buffer) => {
    const line = d.toString().trim();
    if (line) console.log(`[pdf-worker] ${line}`);
  });

  proc.on('exit', (code) => {
    console.warn(`[pdf-worker] process exited (code ${code}) — restarting in 2s`);
    workerProc = null;
    workerReady = false;
    if (!restartTimer) {
      restartTimer = setTimeout(() => {
        restartTimer = null;
        startPdfWorker(serverDir);
      }, 2000);
    }
  });

  proc.on('error', (err) => {
    console.warn(`[pdf-worker] failed to start: ${err.message}`);
    workerProc = null;
    workerReady = false;
  });

  void waitForReady();
}

async function waitForReady(): Promise<void> {
  for (let i = 0; i < 50; i++) {
    try {
      const r = await fetch(`${WORKER_URL}/ping`);
      if (r.ok) {
        workerReady = true;
        console.log('[pdf-worker] ready — PDF jobs now run on the warm worker');
        return;
      }
    } catch {
      // not up yet — keep polling
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  console.warn(
    '[pdf-worker] did not become ready within 10s; falling back to per-request ' +
      'spawn() until it does (it keeps retrying in the background on crash)',
  );
}

// ── Concurrency gate ────────────────────────────────────────────────────
let active = 0;
const waiters: Array<() => void> = [];
let queuedCount = 0;

function acquireSlot(): Promise<() => void> {
  return new Promise((resolve, reject) => {
    const release = () => {
      active--;
      const next = waiters.shift();
      if (next) next();
    };
    if (active < MAX_CONCURRENT) {
      active++;
      resolve(release);
      return;
    }
    if (queuedCount >= MAX_QUEUE) {
      reject(
        new Error('Server is busy processing other documents — please try again in a moment.'),
      );
      return;
    }
    queuedCount++;
    waiters.push(() => {
      queuedCount--;
      active++;
      resolve(release);
    });
  });
}

// ── Minimal ChildProcess-shaped object the 5 route handlers already know
// how to use: .stdout / .stderr as Readable streams, .on('close'|'error').
interface PseudoChildProcess extends EventEmitter {
  stdout: Readable;
  stderr: Readable;
}

function scriptKeyFromPath(scriptPath: string): string {
  return basename(scriptPath, '.py');
}

/** Drop-in for spawn('python3', [scriptPath, ...cliArgs], opts). */
export function runPythonJob(
  args: string[],
  opts: { cwd: string; timeout?: number },
): ChildProcessWithoutNullStreams | PseudoChildProcess {
  if (!workerReady) {
    // Exact legacy path — unchanged behavior when the worker isn't up.
    return spawn('python3', args, {
      cwd: opts.cwd,
      env: {
        ...process.env,
        PATH: `${process.env.PATH ?? ''}:/opt/homebrew/bin:/usr/local/bin:/usr/bin`,
        PYTHONPATH: opts.cwd,
      },
      timeout: opts.timeout ?? 600_000,
    });
  }

  const [scriptPath, ...cliArgs] = args;
  const script = scriptKeyFromPath(scriptPath);

  const emitter = new EventEmitter() as PseudoChildProcess;
  const stdout = new PassThrough();
  const stderr = new PassThrough();
  emitter.stdout = stdout;
  emitter.stderr = stderr;

  void (async () => {
    let release: (() => void) | null = null;
    try {
      release = await acquireSlot();
    } catch (err) {
      emitter.emit('error', err as Error);
      return;
    }
    try {
      const controller = new AbortController();
      const timeoutMs = opts.timeout ?? 600_000;
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      let resp: Response;
      try {
        resp = await fetch(`${WORKER_URL}/run`, {
          method: 'POST',
          body: JSON.stringify({ script, argv: cliArgs }),
          signal: controller.signal,
        });
      } finally {
        clearTimeout(timer);
      }

      const exitCode = Number(resp.headers.get('x-exit-code') ?? '1');
      const stderrB64 = resp.headers.get('x-stderr-b64');
      // Delivered as one chunk at job end (not line-by-line like the old
      // live subprocess pipe) — the worker captures stderr server-side and
      // sends it back once the job finishes. A fine trade for Render log
      // readability in exchange for not re-importing PyMuPDF every request.
      stderr.end(stderrB64 ? Buffer.from(stderrB64, 'base64') : undefined);

      if (resp.body) {
        const nodeStream = Readable.fromWeb(resp.body as never);
        for await (const chunk of nodeStream) {
          stdout.write(chunk as Buffer);
        }
      }
      stdout.end();
      emitter.emit('close', exitCode);
    } catch (err) {
      emitter.emit('error', err as Error);
    } finally {
      release();
    }
  })();

  return emitter;
}
