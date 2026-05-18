import {
  S3Client,
  PutObjectCommand,
  GetObjectCommand,
  DeleteObjectCommand,
} from '@aws-sdk/client-s3';
import { getSignedUrl } from '@aws-sdk/s3-request-presigner';
import { createReadStream, createWriteStream } from 'fs';
import { stat } from 'fs/promises';
import { pipeline } from 'stream/promises';
import type { Readable } from 'stream';

// Object storage for input/output PDFs. The web dyno and the worker dyno are
// separate machines with no shared filesystem, so files must round-trip
// through shared storage. Cloudflare R2 is S3-compatible and has zero egress
// fees (users download large output PDFs — egress would dominate on S3).
//
// Required env (set on BOTH the web service and the worker service):
//   R2_ENDPOINT          https://<account-id>.r2.cloudflarestorage.com
//   R2_ACCESS_KEY_ID
//   R2_SECRET_ACCESS_KEY
//   R2_BUCKET
const ENDPOINT = process.env.R2_ENDPOINT ?? '';
const BUCKET = process.env.R2_BUCKET ?? '';

let _client: S3Client | null = null;

function client(): S3Client {
  if (!ENDPOINT || !process.env.R2_ACCESS_KEY_ID || !process.env.R2_SECRET_ACCESS_KEY || !BUCKET) {
    throw new Error(
      'Object storage not configured — set R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET',
    );
  }
  if (!_client) {
    _client = new S3Client({
      region: 'auto',
      endpoint: ENDPOINT,
      credentials: {
        accessKeyId: process.env.R2_ACCESS_KEY_ID,
        secretAccessKey: process.env.R2_SECRET_ACCESS_KEY,
      },
      forcePathStyle: true,
    });
  }
  return _client;
}

export async function uploadFile(
  key: string,
  localPath: string,
  contentType = 'application/pdf',
): Promise<void> {
  const { size } = await stat(localPath);
  await client().send(
    new PutObjectCommand({
      Bucket: BUCKET,
      Key: key,
      Body: createReadStream(localPath),
      ContentType: contentType,
      ContentLength: size,
    }),
  );
}

export async function downloadFile(key: string, destPath: string): Promise<void> {
  const res = await client().send(new GetObjectCommand({ Bucket: BUCKET, Key: key }));
  if (!res.Body) throw new Error(`Empty object: ${key}`);
  await pipeline(res.Body as Readable, createWriteStream(destPath));
}

export async function presignGetUrl(key: string, expiresInSec = 3600): Promise<string> {
  return getSignedUrl(client(), new GetObjectCommand({ Bucket: BUCKET, Key: key }), {
    expiresIn: expiresInSec,
  });
}

export async function deleteObject(key: string): Promise<void> {
  try {
    await client().send(new DeleteObjectCommand({ Bucket: BUCKET, Key: key }));
  } catch {
    // Best-effort cleanup — a leaked temp object is not worth failing a job.
  }
}

export function storageConfigured(): boolean {
  return Boolean(
    ENDPOINT &&
      process.env.R2_ACCESS_KEY_ID &&
      process.env.R2_SECRET_ACCESS_KEY &&
      BUCKET,
  );
}
