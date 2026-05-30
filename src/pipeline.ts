import { spawn } from 'child_process';
import { createWriteStream } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));
// server.ts lives at repo root; the python pipeline is in ./server next to it.
const REPO_ROOT = join(__dirname, '..');
const PY_SCRIPT = join(REPO_ROOT, 'server', 'error_detector.py');
const PY_CWD = join(REPO_ROOT, 'server');

export interface PipelineInput {
  mainPaths: string[];
  annexPaths: string[];
  clientSigPath?: string;
  advocateSigPath?: string;
  indexEndPage: number;
  /** Optional --sign-pages spec passed through to error_detector.py. */
  signPages?: string;
  // Local path the finished PDF is written to.
  outputPath: string;
}

// Runs the exact same `error_detector.py --mode write --write-stdout`
// invocation the legacy sync endpoint used, but pipes the produced PDF to a
// file instead of an HTTP response. Resolves on exit 0; rejects with the
// captured stderr otherwise.
export function runWritePagination(input: PipelineInput): Promise<void> {
  return new Promise((resolve, reject) => {
    const args = [PY_SCRIPT];
    for (const p of input.mainPaths) args.push('--file', p);
    for (const p of input.annexPaths) args.push('--annex', p);
    if (input.clientSigPath) args.push('--client-sig', input.clientSigPath);
    if (input.advocateSigPath) args.push('--advocate-sig', input.advocateSigPath);
    args.push('--index-end-page', String(input.indexEndPage));
    if (input.signPages && input.signPages.trim()) {
      args.push('--sign-pages', input.signPages.trim());
    }
    args.push('--mode', 'write');
    args.push('--write-stdout');

    const proc = spawn('python3', args, {
      cwd: PY_CWD,
      env: {
        ...process.env,
        PATH: `${process.env.PATH ?? ''}:/opt/homebrew/bin:/usr/local/bin:/usr/bin`,
        PYTHONPATH: PY_CWD,
      },
      timeout: 600_000,
    });

    const out = createWriteStream(input.outputPath);
    proc.stdout.pipe(out);

    let stderrBuf = '';
    proc.stderr.on('data', (d: Buffer) => {
      const line = d.toString().trim();
      if (line) console.log(`[pipeline] ${line}`);
      stderrBuf += line + '\n';
    });

    let settled = false;
    const fail = (msg: string) => {
      if (settled) return;
      settled = true;
      out.destroy();
      reject(new Error(msg));
    };

    proc.on('error', (err) => fail(`Process error: ${err.message}`));

    proc.on('close', (code) => {
      if (settled) return;
      if (code !== 0) {
        fail(stderrBuf.trim() || `python exited ${code}`);
        return;
      }
      out.on('finish', () => {
        settled = true;
        resolve();
      });
      out.on('error', (e) => fail(`Output write error: ${e.message}`));
      out.end();
    });
  });
}
