import { useCallback } from "react";
import { useTranslation } from "react-i18next";
import { ARWorkInstructions, type WorkInstructionStep } from "./ARWorkInstructions";
import { apiRequest, ForbiddenError } from "../services/api";

interface Props {
  vin: string;
  userId: string;
  onForbidden: (resource: string) => void;
}

async function fetchInstructions(
  vin: string,
  signal: AbortSignal,
  userId: string,
  onForbidden: (resource: string) => void,
): Promise<WorkInstructionStep[]> {
  const resource = `/api/v1/ar/instructions/${vin}`;
  try {
    return await apiRequest<WorkInstructionStep[]>(resource, { signal, userId });
  } catch (err) {
    if (err instanceof ForbiddenError) {
      onForbidden(err.resource);
      return [];
    }
    throw err;
  }
}

/**
 * AR Workbench page — displays work instructions for Assembly Associates.
 * Calls GET /api/v1/ar/instructions/{vin}. Propagates 403 responses to the
 * App-level forbidden screen via onForbidden.
 */
export function ARWorkbenchPage({ vin, userId, onForbidden }: Props) {
  const { t } = useTranslation();

  const fetchFn = useCallback(
    (v: string, signal: AbortSignal) => fetchInstructions(v, signal, userId, onForbidden),
    [userId, onForbidden],
  );

  return (
    <div className="ar-workbench-page">
      <h2 className="ar-workbench-heading">{t("ar_workbench.title")}</h2>
      <ARWorkInstructions vin={vin} fetchInstructions={fetchFn} />
    </div>
  );
}
