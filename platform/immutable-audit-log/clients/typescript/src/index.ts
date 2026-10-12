/**
 * audit-client — append-only write SDK for the iFactoryOS immutable audit log.
 *
 * Writes structured audit events to Azure Blob Storage (WORM) using Managed
 * Identity authentication. before_hash / after_hash must be SHA-256 hex
 * digests computed by the caller from the respective state values.
 *
 * Required environment variables:
 *   AUDIT_STORAGE_ACCOUNT — Azure Storage account name
 *   AUDIT_CONTAINER_NAME  — Blob container name (default: audit-events)
 */

import { ManagedIdentityCredential } from "@azure/identity";
import { BlobServiceClient } from "@azure/storage-blob";
import { randomUUID } from "node:crypto";

const SHA256_RE = /^[0-9a-f]{64}$/;
const WRITE_TIMEOUT_MS = 1_000;

export interface AuditEventInput {
  user_id: string;
  action_type: string;
  entity_id: string;
  /** SHA-256 hex digest of the caller-supplied before-state */
  before_hash: string;
  /** SHA-256 hex digest of the caller-supplied after-state */
  after_hash: string;
  /** ISO-8601 UTC timestamp */
  utc_ts: string;
}

export class WriteLatencyExceeded extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WriteLatencyExceeded";
  }
}

export class AuditClient {
  private readonly containerName: string;
  private readonly blobService: BlobServiceClient;

  constructor(
    storageAccount?: string,
    containerName?: string,
  ) {
    const account = storageAccount ?? requireEnv("AUDIT_STORAGE_ACCOUNT");
    this.containerName = containerName ?? process.env["AUDIT_CONTAINER_NAME"] ?? "audit-events";
    const accountUrl = `https://${account}.blob.core.windows.net`;
    this.blobService = new BlobServiceClient(accountUrl, new ManagedIdentityCredential());
  }

  /**
   * Append one audit event blob and return the blob name.
   *
   * Throws WriteLatencyExceeded if the upload takes longer than 1 second.
   * Throws Error for malformed before_hash / after_hash.
   */
  async write_event(
    user_id: string,
    action_type: string,
    entity_id: string,
    before_hash: string,
    after_hash: string,
    utc_ts: string,
  ): Promise<string> {
    validateSha256(before_hash, "before_hash");
    validateSha256(after_hash, "after_hash");

    const event: AuditEventInput = {
      user_id,
      action_type,
      entity_id,
      before_hash,
      after_hash,
      utc_ts,
    };

    const blobName = makeBlobName(utc_ts, entity_id);
    const payload = JSON.stringify(event);

    const containerClient = this.blobService.getContainerClient(this.containerName);
    const blobClient = containerClient.getBlockBlobClient(blobName);

    const t0 = performance.now();
    await blobClient.upload(payload, Buffer.byteLength(payload, "utf8"), {
      // overwrite: false is not directly in the BlockBlobClient.upload signature;
      // WORM is enforced at the Azure storage layer. The client enforces append-only
      // semantics by using a unique blob name per event (UUID suffix).
      blobHTTPHeaders: { blobContentType: "application/json" },
    });
    const elapsed = performance.now() - t0;

    if (elapsed > WRITE_TIMEOUT_MS) {
      throw new WriteLatencyExceeded(
        `write_event took ${elapsed.toFixed(0)}ms, exceeds 1 s SLA`,
      );
    }

    return blobName;
  }
}

/** Module-level convenience function; reads config from environment. */
export async function write_event(
  user_id: string,
  action_type: string,
  entity_id: string,
  before_hash: string,
  after_hash: string,
  utc_ts: string,
): Promise<string> {
  return new AuditClient().write_event(
    user_id,
    action_type,
    entity_id,
    before_hash,
    after_hash,
    utc_ts,
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function validateSha256(value: string, field: string): void {
  if (!SHA256_RE.test(value)) {
    throw new Error(
      `${field} must be a lowercase 64-character SHA-256 hex digest, got: ${JSON.stringify(value)}`,
    );
  }
}

function makeBlobName(utcTs: string, entityId: string): string {
  const safeTs = utcTs.slice(0, 19).replace(/:/g, "-").replace("T", "/");
  return `${safeTs}/${entityId}/${randomUUID()}.json`;
}

function requireEnv(name: string): string {
  const value = process.env[name];
  if (value === undefined || value === "") {
    throw new Error(`Required environment variable ${name} is not set`);
  }
  return value;
}
