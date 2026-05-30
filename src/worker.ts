import 'dotenv/config';
import { Worker, type Job } from 'bullmq';
import { mkdtemp, rm } from 'fs/promises';
import { tmpdir } from 'os';
import { join } from 'path';
import {
  QUEUE_NAME,
  connection,
  type WritePaginationJob,
  type JobResult,
  type JobFileRef,
} from './queue';
import { downloadFile, uploadFile, deleteObject } from './storage';
import { runWritePagination } from './pipeline';

// Heavy PDF jobs are memory + CPU bound. On a 2GB Render Standard worker,
// ~2 concurrent 50-100MB jobs is the safe ceiling before OOM. Tune via env
// per the instance size (Pro/4GB can go to 3-4).
const CONCURRENCY = Number.parseInt(process.env.WORKER_CONCURRENCY ?? '2', 10);

async function processJob(job: Job<WritePaginationJob, JobResult>): Promise<JobResult> {
  const data = job.data;
  const work = await mkdtemp(join(tmpdir(), 'nomikos-job-'));
  const localOf = (ref: JobFileRef, i: number, kind: string) =>
    join(work, `${kind}-${i}-${ref.originalName.replace(/[^\w.-]/g, '_')}`);

  const allInputKeys: string[] = [];
  try {
    await job.updateProgress(5);

    const mainPaths: string[] = [];
    for (let i = 0; i < data.main.length; i++) {
      const p = localOf(data.main[i], i, 'main');
      await downloadFile(data.main[i].key, p);
      mainPaths.push(p);
      allInputKeys.push(data.main[i].key);
    }

    const annexPaths: string[] = [];
    for (let i = 0; i < data.annex.length; i++) {
      const p = localOf(data.annex[i], i, 'annex');
      await downloadFile(data.annex[i].key, p);
      annexPaths.push(p);
      allInputKeys.push(data.annex[i].key);
    }

    let clientSigPath: string | undefined;
    if (data.clientSig) {
      clientSigPath = localOf(data.clientSig, 0, 'csig');
      await downloadFile(data.clientSig.key, clientSigPath);
      allInputKeys.push(data.clientSig.key);
    }
    let advocateSigPath: string | undefined;
    if (data.advocateSig) {
      advocateSigPath = localOf(data.advocateSig, 0, 'asig');
      await downloadFile(data.advocateSig.key, advocateSigPath);
      allInputKeys.push(data.advocateSig.key);
    }

    await job.updateProgress(25);

    const outputPath = join(work, 'output.pdf');
    await runWritePagination({
      mainPaths,
      annexPaths,
      clientSigPath,
      advocateSigPath,
      indexEndPage: data.indexEndPage,
      signPages: data.signPages,
      outputPath,
    });

    await job.updateProgress(85);

    await uploadFile(data.outputKey, outputPath, 'application/pdf');
    await job.updateProgress(100);

    return { outputKey: data.outputKey, downloadName: data.downloadName };
  } finally {
    // Always clean the local scratch dir; best-effort purge of the input
    // objects (output is kept until the user downloads / lifecycle expiry).
    await rm(work, { recursive: true, force: true }).catch(() => {});
    for (const k of allInputKeys) await deleteObject(k);
  }
}

const worker = new Worker<WritePaginationJob, JobResult>(QUEUE_NAME, processJob, {
  connection: connection(),
  concurrency: CONCURRENCY,
});

worker.on('ready', () => {
  console.log(`[worker] ready — queue="${QUEUE_NAME}" concurrency=${CONCURRENCY}`);
});
worker.on('active', (job) => console.log(`[worker] job ${job.id} started`));
worker.on('completed', (job) => console.log(`[worker] job ${job.id} completed`));
worker.on('failed', (job, err) =>
  console.error(`[worker] job ${job?.id} failed: ${err.message}`),
);
worker.on('error', (err) => console.error(`[worker] error: ${err.message}`));

const shutdown = async () => {
  console.log('[worker] shutting down…');
  await worker.close();
  process.exit(0);
};
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
