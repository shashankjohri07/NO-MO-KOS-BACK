import 'dotenv/config';
import express, { Request, Response } from 'express';
import cors from 'cors';
import multer from 'multer';
import { spawn } from 'child_process';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import fs from 'fs';

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
  { name: 'advocateSignature', maxCount: 1 },
  // Optional PNG/JPG signatures for the SPECIAL main-document pages listed in
  // signPages. Separate images from the annexure signatures above — these let
  // the user sign the vakalatnama / prayer page / affidavit with a distinct
  // signature set, independent of the every-annexure-page stamps.
  { name: 'specialSignatureClient', maxCount: 1 },
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
app.post('/api/write-pagination', uploadDualFields, (req: Request, res: Response) => {
  const fileMap = (req.files as Record<string, Express.Multer.File[]> | undefined) ?? {};
  const mainFiles = fileMap.document ?? [];
  const annexFiles = fileMap.annex ?? [];
  const clientSig = fileMap.clientSignature?.[0];
  const advocateSig = fileMap.advocateSignature?.[0];
  const specialClientSig = fileMap.specialSignatureClient?.[0];
  const specialAdvocateSig = fileMap.specialSignatureAdvocate?.[0];
  if (mainFiles.length === 0) {
    res.status(400).json({ ok: false, error: 'No file uploaded' });
    return;
  }

  const mainPaths = mainFiles.map((f) => f.path);
  const annexPaths = annexFiles.map((f) => f.path);
  const sigPaths = [
    clientSig?.path,
    advocateSig?.path,
    specialClientSig?.path,
    specialAdvocateSig?.path,
  ].filter(Boolean) as string[];
  const cleanup = () => {
    for (const p of [...mainPaths, ...annexPaths, ...sigPaths]) fs.unlink(p, () => {});
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
    advocateSig ? 'advocate-sig' : null,
    specialClientSig ? 'special-client-sig' : null,
    specialAdvocateSig ? 'special-advocate-sig' : null,
  ]
    .filter(Boolean)
    .join('+');
  const totalMB =
    [
      ...mainFiles,
      ...annexFiles,
      ...(clientSig ? [clientSig] : []),
      ...(advocateSig ? [advocateSig] : []),
      ...(specialClientSig ? [specialClientSig] : []),
      ...(specialAdvocateSig ? [specialAdvocateSig] : []),
    ].reduce((acc, f) => acc + f.size, 0) / 1024 / 1024;
  console.log(
    `[write-pagination] ${mainFiles.length} main(s): ${mainNames}${annexSummary}${sigSummary ? ' + ' + sigSummary : ''} (${totalMB.toFixed(1)}MB) — indexEndPage=${indexEndPage}${signPages ? `, signPages='${signPages}'` : ''}`,
  );

  const args = [join(__dirname, 'server', 'error_detector.py')];
  for (const p of mainPaths) args.push('--file', p);
  for (const p of annexPaths) args.push('--annex', p);
  if (clientSig) args.push('--client-sig', clientSig.path);
  if (advocateSig) args.push('--advocate-sig', advocateSig.path);
  if (specialClientSig) args.push('--special-sig-client', specialClientSig.path);
  if (specialAdvocateSig) args.push('--special-sig-advocate', specialAdvocateSig.path);
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

app.listen(PORT, () => {
  console.log(`API server running on http://localhost:${PORT}`);
});
