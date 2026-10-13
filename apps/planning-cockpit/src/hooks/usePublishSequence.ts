import { useCallback, useEffect, useRef, useState } from "react";
import type {
  SequencePublishRequest,
  SequencePublishResponse,
  SyncJob,
} from "../types/sequence";
import { publishSequence, getSyncJob, listSyncJobs } from "../services/api";

export type PublishState =
  | { phase: "idle" }
  | { phase: "publishing" }
  | { phase: "published"; response: SequencePublishResponse }
  | {
      phase: "queued";
      response: SequencePublishResponse;
      syncJobId: string;
      retryScheduledAt: string | null;
    }
  | { phase: "error"; message: string; isRejected: boolean };

const POLL_INTERVAL_MS = 30_000;
const PENDING_KEY = "ifos_pending_publish";

interface PendingPublishRecord {
  syncJobId: string;
  sequencePlanId: string;
  retryScheduledAt: string | null;
}

function emitAdoptionEvent(
  userId: string,
  sessionId: string,
  opcenterSequenceId: string,
  publishedAt: string,
): void {
  const event = {
    action: "publish_confirmed",
    userId,
    sessionId,
    opcenterSequenceId,
    publishedAt,
    timestamp: new Date().toISOString(),
  };
  if (
    typeof window !== "undefined" &&
    typeof (
      window as Window & { otelClient?: { track: (e: unknown) => void } }
    ).otelClient?.track === "function"
  ) {
    (
      window as Window & { otelClient: { track: (e: unknown) => void } }
    ).otelClient.track(event);
  }
  window.dispatchEvent(new CustomEvent("ifos:adoption", { detail: event }));
}

function getUserIdFromToken(): string {
  try {
    const token = localStorage.getItem("ifos_planning_cockpit_token");
    if (!token) return "anonymous";
    const payload = token.split(".")[1];
    if (!payload) return "anonymous";
    const decoded = JSON.parse(atob(payload)) as Record<string, unknown>;
    return (decoded.sub as string) ?? "anonymous";
  } catch {
    return "anonymous";
  }
}

function getSessionId(): string {
  const key = "ifos_session_id";
  const existing = sessionStorage.getItem(key);
  if (existing) return existing;
  const id = "session-" + Math.random().toString(36).slice(2);
  sessionStorage.setItem(key, id);
  return id;
}

export interface UsePublishSequenceResult {
  publishState: PublishState;
  publish: (req: SequencePublishRequest) => Promise<void>;
  dismiss: () => void;
}

export function usePublishSequence(
  sequencePlanId: string,
): UsePublishSequenceResult {
  const [publishState, setPublishState] = useState<PublishState>({
    phase: "idle",
  });
  // Separate tracked syncJobId so the polling effect has a stable scalar dep
  const [pollingSyncJobId, setPollingSyncJobId] = useState<string | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Restore pending publish from localStorage on mount
  useEffect(() => {
    const raw = localStorage.getItem(PENDING_KEY);
    if (!raw) return;
    try {
      const record = JSON.parse(raw) as PendingPublishRecord;
      if (record.sequencePlanId !== sequencePlanId) return;
      setPublishState({
        phase: "queued",
        response: {
          syncJobId: record.syncJobId,
          opcenterSequenceId: null,
          status: "queued",
          source: "planning-cockpit",
          publishedAt: null,
          retryScheduledAt: record.retryScheduledAt,
        },
        syncJobId: record.syncJobId,
        retryScheduledAt: record.retryScheduledAt,
      });
      setPollingSyncJobId(record.syncJobId);
    } catch {
      localStorage.removeItem(PENDING_KEY);
    }
  }, [sequencePlanId]);

  // Restore any queued jobs for this plan from the server on mount
  useEffect(() => {
    let cancelled = false;
    listSyncJobs({ status: "queued" })
      .then((res) => {
        if (cancelled) return;
        const match = res.data.find(
          (j) => j.sequencePlanId === sequencePlanId,
        );
        if (!match) return;
        setPublishState((prev) => {
          if (prev.phase !== "idle") return prev;
          const record: PendingPublishRecord = {
            syncJobId: match.syncJobId,
            sequencePlanId: match.sequencePlanId,
            retryScheduledAt: match.nextRetryAt ?? null,
          };
          localStorage.setItem(PENDING_KEY, JSON.stringify(record));
          return {
            phase: "queued",
            response: {
              syncJobId: match.syncJobId,
              opcenterSequenceId: null,
              status: "queued",
              source: "planning-cockpit",
              publishedAt: null,
              retryScheduledAt: match.nextRetryAt ?? null,
            },
            syncJobId: match.syncJobId,
            retryScheduledAt: match.nextRetryAt ?? null,
          };
        });
        setPollingSyncJobId(match.syncJobId);
      })
      .catch(() => {
        // non-fatal — offline restore from localStorage is the fallback
      });
    return () => {
      cancelled = true;
    };
  }, [sequencePlanId]);

  // Start polling when queued; stop when pollingSyncJobId becomes null
  useEffect(() => {
    if (!pollingSyncJobId) return;

    const syncJobId = pollingSyncJobId;

    async function poll() {
      let job: SyncJob;
      try {
        job = await getSyncJob(syncJobId);
      } catch {
        return;
      }

      if (job.status === "published") {
        localStorage.removeItem(PENDING_KEY);
        const response: SequencePublishResponse = {
          syncJobId: job.syncJobId,
          opcenterSequenceId: job.opcenterSequenceId ?? null,
          status: "published",
          source: "planning-cockpit",
          publishedAt: job.lastAttemptAt ?? null,
          retryScheduledAt: null,
        };
        setPublishState({ phase: "published", response });
        setPollingSyncJobId(null);
        emitAdoptionEvent(
          getUserIdFromToken(),
          getSessionId(),
          job.opcenterSequenceId ?? "",
          job.lastAttemptAt ?? new Date().toISOString(),
        );
      } else if (
        job.status === "failed" ||
        job.status === "rejected_by_opcenter"
      ) {
        localStorage.removeItem(PENDING_KEY);
        setPublishState({
          phase: "error",
          message: job.opcenterError ?? "Publish failed",
          isRejected: job.status === "rejected_by_opcenter",
        });
        setPollingSyncJobId(null);
      } else if (job.nextRetryAt) {
        setPublishState((prev) => {
          if (prev.phase !== "queued") return prev;
          return { ...prev, retryScheduledAt: job.nextRetryAt ?? null };
        });
      }
    }

    pollRef.current = setInterval(() => {
      void poll();
    }, POLL_INTERVAL_MS);

    return () => {
      if (pollRef.current !== null) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [pollingSyncJobId]);

  const publish = useCallback(
    async (req: SequencePublishRequest): Promise<void> => {
      setPublishState({ phase: "publishing" });
      setPollingSyncJobId(null);

      let response: SequencePublishResponse;
      try {
        response = await publishSequence(req);
      } catch (err) {
        let rawBody = err instanceof Error ? err.message : "Request failed";
        try {
          const parsed = JSON.parse(rawBody) as { message?: string };
          if (parsed.message) rawBody = parsed.message;
        } catch {
          // leave rawBody as-is
        }
        setPublishState({
          phase: "error",
          message: rawBody,
          isRejected: false,
        });
        return;
      }

      if (response.status === "published") {
        localStorage.removeItem(PENDING_KEY);
        setPublishState({ phase: "published", response });
        emitAdoptionEvent(
          getUserIdFromToken(),
          getSessionId(),
          response.opcenterSequenceId ?? "",
          response.publishedAt ?? new Date().toISOString(),
        );
      } else if (response.status === "queued") {
        const record: PendingPublishRecord = {
          syncJobId: response.syncJobId,
          sequencePlanId: req.sequencePlanId,
          retryScheduledAt: response.retryScheduledAt ?? null,
        };
        localStorage.setItem(PENDING_KEY, JSON.stringify(record));
        setPublishState({
          phase: "queued",
          response,
          syncJobId: response.syncJobId,
          retryScheduledAt: response.retryScheduledAt ?? null,
        });
        setPollingSyncJobId(response.syncJobId);
      } else {
        setPublishState({
          phase: "error",
          message: "Publish failed — unknown status",
          isRejected: false,
        });
      }
    },
    [],
  );

  const dismiss = useCallback(() => {
    setPollingSyncJobId(null);
    setPublishState({ phase: "idle" });
  }, []);

  useEffect(() => {
    return () => {
      if (pollRef.current !== null) clearInterval(pollRef.current);
    };
  }, []);

  return { publishState, publish, dismiss };
}
