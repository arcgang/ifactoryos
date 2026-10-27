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

const PAGE_SESSION_ID =
  typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : "session-" + Math.random().toString(36).slice(2);

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
      window as unknown as Window & { otelClient: { track: (e: unknown) => void } }
    ).otelClient.track(event);
  }
  window.dispatchEvent(new CustomEvent("ifos:adoption", { detail: event }));
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
      .catch((err: unknown) => {
        console.warn("usePublishSequence: failed to restore queued jobs", err);
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
      } catch (err: unknown) {
        console.warn("usePublishSequence: poll failed, will retry", err);
        return;
      }

      if (job.status === "published") {
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
          "anonymous",
          PAGE_SESSION_ID,
          job.opcenterSequenceId ?? "",
          job.lastAttemptAt ?? new Date().toISOString(),
        );
      } else if (
        job.status === "failed" ||
        job.status === "rejected_by_opcenter"
      ) {
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
        } catch (parseErr: unknown) {
          console.debug("usePublishSequence: error body is not JSON", parseErr);
        }
        setPublishState({
          phase: "error",
          message: rawBody,
          isRejected: false,
        });
        return;
      }

      if (response.status === "published") {
        setPublishState({ phase: "published", response });
        emitAdoptionEvent(
          "anonymous",
          PAGE_SESSION_ID,
          response.opcenterSequenceId ?? "",
          response.publishedAt ?? new Date().toISOString(),
        );
      } else if (response.status === "queued") {
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
