/**
 * Tests for audit-client (TypeScript).
 *
 * Covers:
 *   1. WORM semantics: each write uses a unique blob name (no overwrite possible)
 *   2. Lifecycle/delete: verifies no delete is ever called on the blob client
 *   3. Blob schema: all six required fields in the uploaded JSON payload
 *   4. Latency gate: write_event throws WriteLatencyExceeded when upload is slow
 */

import { describe, it, expect } from "vitest";
import { createHash } from "node:crypto";
import { AuditClient, WriteLatencyExceeded } from "../src/index.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const ANCHOR_TS = "2030-01-01T00:00:00Z"; // far-future constant — never today

function sha256(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}

const BEFORE_HASH = sha256("before-state");
const AFTER_HASH = sha256("after-state");

// ---------------------------------------------------------------------------
// Fake Azure SDK — injected directly into AuditClient._blobService
// ---------------------------------------------------------------------------

interface UploadCall {
  payload: string;
  blobName: string;
}

class FakeBlobClient {
  readonly uploads: UploadCall[] = [];
  readonly deleteCalls: string[] = [];
  private readonly _name: string;

  constructor(blobName: string, _parent: FakeBlobServiceClient) {
    this._name = blobName;
  }

  async upload(data: string, _size: number, _opts?: unknown): Promise<void> {
    this.uploads.push({ payload: data, blobName: this._name });
    (this as unknown as { _parent: FakeBlobServiceClient })._parent?.uploads?.push({
      payload: data,
      blobName: this._name,
    });
  }

  async delete(): Promise<void> {
    this.deleteCalls.push(this._name);
    throw new Error("WORM policy: deletion is not permitted during retention period");
  }
}

class FakeBlobServiceClient {
  readonly uploads: UploadCall[] = [];
  readonly deleteCalls: string[] = [];
  private readonly _blobs = new Map<string, FakeBlobClient>();

  getContainerClient(_name: string) {
    const self = this;
    return {
      getBlockBlobClient: (blobName: string): FakeBlobClient => {
        if (!self._blobs.has(blobName)) {
          const bc = new FakeBlobClient(blobName, self);
          // Wire upload to also record in service-level list
          const origUpload = bc.upload.bind(bc);
          bc.upload = async (data: string, size: number, opts?: unknown) => {
            await origUpload(data, size, opts);
            self.uploads.push({ payload: data, blobName });
          };
          self._blobs.set(blobName, bc);
        }
        return self._blobs.get(blobName)!;
      },
    };
  }

  blobCount(): number {
    return this._blobs.size;
  }
}

/** Build an AuditClient with a fake BlobServiceClient injected. */
function makeClient(): { client: AuditClient; fake: FakeBlobServiceClient } {
  const fake = new FakeBlobServiceClient();
  const client = new AuditClient("stauditlogtest", "audit-events");
  (client as unknown as { blobService: unknown }).blobService = fake;
  return { client, fake };
}

// ---------------------------------------------------------------------------
// WORM overwrite semantics
// ---------------------------------------------------------------------------

describe("WORM overwrite semantics", () => {
  it("each write_event call produces a unique blob name so overwrite cannot occur", async () => {
    const { client, fake } = makeClient();

    await client.write_event(
      "u-001", "SEQ_UPDATED", "VIN-TESTVIN00000001A",
      BEFORE_HASH, AFTER_HASH, ANCHOR_TS,
    );
    await client.write_event(
      "u-001", "SEQ_UPDATED", "VIN-TESTVIN00000001A",
      BEFORE_HASH, AFTER_HASH, ANCHOR_TS,
    );

    const names = fake.uploads.map((u) => u.blobName);
    expect(names).toHaveLength(2);
    expect(names[0]).not.toBe(names[1]);
  });
});

// ---------------------------------------------------------------------------
// WORM delete rejection
// ---------------------------------------------------------------------------

describe("WORM delete rejection", () => {
  it("blob client delete rejects — simulates Azure WORM retention enforcement", async () => {
    const { client, fake } = makeClient();

    await client.write_event(
      "u-001", "VIN_CREATED", "VIN-TESTVIN00000001A",
      BEFORE_HASH, AFTER_HASH, ANCHOR_TS,
    );

    const { blobName } = fake.uploads[0];
    const container = fake.getContainerClient("audit-events");
    const blobClient = container.getBlockBlobClient(blobName);
    await expect(blobClient.delete()).rejects.toThrow("deletion is not permitted");
  });

  it("write_event never calls delete on the blob client", async () => {
    const { client, fake } = makeClient();

    await client.write_event(
      "u-002", "CERT_ISSUED", "CERT-001",
      BEFORE_HASH, AFTER_HASH, ANCHOR_TS,
    );

    expect(fake.deleteCalls).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Blob schema: all six required fields
// ---------------------------------------------------------------------------

describe("blob schema", () => {
  it("uploaded JSON contains all six required fields with the correct values", async () => {
    const { client, fake } = makeClient();

    await client.write_event(
      "u-operator-99",
      "SEQUENCE_FINALISED",
      "VIN-ABCDEFGHJKLMNPRA",
      BEFORE_HASH,
      AFTER_HASH,
      ANCHOR_TS,
    );

    expect(fake.uploads).toHaveLength(1);
    const payload = JSON.parse(fake.uploads[0].payload);

    expect(payload.user_id).toBe("u-operator-99");
    expect(payload.action_type).toBe("SEQUENCE_FINALISED");
    expect(payload.entity_id).toBe("VIN-ABCDEFGHJKLMNPRA");
    expect(payload.before_hash).toBe(BEFORE_HASH);
    expect(payload.after_hash).toBe(AFTER_HASH);
    expect(payload.utc_ts).toBe(ANCHOR_TS);
  });

  it("payload contains no extra fields beyond the required six", async () => {
    const { client, fake } = makeClient();

    await client.write_event(
      "u-999", "CERT_SIGNED", "CERT-002",
      BEFORE_HASH, AFTER_HASH, ANCHOR_TS,
    );

    const payload = JSON.parse(fake.uploads[0].payload);
    expect(Object.keys(payload).sort()).toEqual(
      ["action_type", "after_hash", "before_hash", "entity_id", "user_id", "utc_ts"],
    );
  });
});

// ---------------------------------------------------------------------------
// Latency gate
// ---------------------------------------------------------------------------

describe("write latency gate", () => {
  it("throws WriteLatencyExceeded when upload takes longer than 1 s", async () => {
    const fake = new FakeBlobServiceClient();
    const client = new AuditClient("stauditlogtest", "audit-events");

    // Override the container client to simulate a slow upload
    (fake as unknown as {
      getContainerClient: (name: string) => unknown;
    }).getContainerClient = (_name: string) => ({
      getBlockBlobClient: (_blobName: string) => ({
        upload: async (_data: string, _size: number, _opts?: unknown): Promise<void> => {
          await new Promise((r) => setTimeout(r, 1_100));
        },
        delete: async (): Promise<void> => {
          throw new Error("WORM: delete rejected");
        },
      }),
    });

    (client as unknown as { blobService: unknown }).blobService = fake;

    await expect(
      client.write_event(
        "u-001", "SLOW_ACTION", "E-001",
        BEFORE_HASH, AFTER_HASH, ANCHOR_TS,
      ),
    ).rejects.toBeInstanceOf(WriteLatencyExceeded);
  }, 5_000);

  it("returns blob name and does not throw when upload completes within 1 s", async () => {
    const { client } = makeClient();

    const blobName = await client.write_event(
      "u-fast", "FAST_ACTION", "E-FAST",
      BEFORE_HASH, AFTER_HASH, ANCHOR_TS,
    );
    expect(blobName).toBeTruthy();
    expect(blobName).toMatch(/\.json$/);
  });
});

// ---------------------------------------------------------------------------
// Input validation
// ---------------------------------------------------------------------------

describe("input validation", () => {
  it("throws when before_hash is not a 64-character hex digest", async () => {
    const { client } = makeClient();

    await expect(
      client.write_event("u-001", "X", "E-001", "not-a-hash", AFTER_HASH, ANCHOR_TS),
    ).rejects.toThrow("before_hash");
  });

  it("throws when after_hash is not a 64-character hex digest", async () => {
    const { client } = makeClient();

    await expect(
      client.write_event("u-001", "X", "E-001", BEFORE_HASH, "short", ANCHOR_TS),
    ).rejects.toThrow("after_hash");
  });
});

// ---------------------------------------------------------------------------
// Instance isolation
// ---------------------------------------------------------------------------

describe("instance isolation", () => {
  it("two AuditClient instances have independent upload state", async () => {
    const { client: clientA, fake: fakeA } = makeClient();
    const { client: clientB, fake: fakeB } = makeClient();

    await clientA.write_event(
      "ua", "ACTION_A", "EA",
      BEFORE_HASH, AFTER_HASH, ANCHOR_TS,
    );

    expect(fakeA.uploads).toHaveLength(1);
    expect(fakeB.uploads).toHaveLength(0);
  });
});
