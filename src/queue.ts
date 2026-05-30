import { Queue, type ConnectionOptions } from 'bullmq';
import IORedis from 'ioredis';

// BullMQ-backed job queue. The web service enqueues; the worker service
// consumes. Both connect to the same Redis.
//
// Required env (set on BOTH services):
//   REDIS_URL   e.g. rediss://default:<pwd>@<host>:6379  (Upstash / Render Redis)
const REDIS_URL = process.env.REDIS_URL ?? '';

export const QUEUE_NAME = 'write-pagination';

// One uploaded file's location in object storage + its original name (the
// pipeline derives the download filename from the first main file's name).
export interface JobFileRef {
  key: string;
  originalName: string;
}

export interface WritePaginationJob {
  main: JobFileRef[];
  annex: JobFileRef[];
  clientSig?: JobFileRef;
  advocateSig?: JobFileRef;
  indexEndPage: number;
  // Optional comma+range spec ("1, 3-5, 8") of additional MAIN-document
  // page numbers — by their stamped digit — that should also receive
  // client/advocate signatures. Annexures are already auto-signed on
  // every page, so out-of-main-range entries are silently dropped by
  // the Python CLI. Empty/omitted = behave like before.
  signPages?: string;
  // Where the worker should write the finished PDF in object storage.
  outputKey: string;
  downloadName: string;
}

export interface JobResult {
  outputKey: string;
  downloadName: string;
}

let _connection: IORedis | null = null;

export function redisConfigured(): boolean {
  return Boolean(REDIS_URL);
}

// BullMQ requires maxRetriesPerRequest: null on the shared connection.
export function connection(): ConnectionOptions {
  if (!REDIS_URL) {
    throw new Error('Queue not configured — set REDIS_URL');
  }
  if (!_connection) {
    _connection = new IORedis(REDIS_URL, { maxRetriesPerRequest: null });
  }
  return _connection;
}

let _queue: Queue<WritePaginationJob, JobResult> | null = null;

export function paginationQueue(): Queue<WritePaginationJob, JobResult> {
  if (!_queue) {
    _queue = new Queue<WritePaginationJob, JobResult>(QUEUE_NAME, {
      connection: connection(),
      defaultJobOptions: {
        attempts: 2,
        backoff: { type: 'exponential', delay: 5000 },
        // Keep finished jobs around briefly so the frontend can still poll
        // status + fetch the result URL after completion.
        removeOnComplete: { age: 3600, count: 1000 },
        removeOnFail: { age: 24 * 3600 },
      },
    });
  }
  return _queue;
}
