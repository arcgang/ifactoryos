export type DefectClass = "dust" | "orange_peel" | "run" | "crater" | "pinhole";

export type ClusterStatus = "open" | "in_progress" | "resolved";

export type AlertStatus = "new" | "acknowledged" | "in_progress" | "resolved";

export interface RootCauseFactor {
  rank: number;
  factor_name: string;
  factor_category: "prep_stage" | "booth_parameter" | "climate_condition";
  probability_score: number;
  supporting_vin_count: number;
}

export interface ClusterAlert {
  alert_id: string;
  cluster_id: string;
  defect_class: DefectClass;
  status: AlertStatus;
  affected_vin_count: number;
  root_cause_signature: string;
  confidence_score: number;
  most_recent_vin: string;
  process_replay_url: string;
  triggered_at: string;
  created_at: string;
  updated_at: string;
  acknowledged_at: string | null;
  acknowledged_by_user_id: string | null;
  corrective_action_jira_id: string | null;
}

export interface CursorPage {
  next_cursor: string | null;
  limit: number;
}

export interface AlertListResponse {
  data: ClusterAlert[];
  page: CursorPage;
}

export interface AcknowledgeAlertRequest {
  acknowledged_by_user_id: string;
  notes?: string;
  corrective_action_jira_id?: string;
}

export interface DefectCluster {
  cluster_id: string;
  defect_class: DefectClass;
  status: ClusterStatus;
  root_cause_signature: string;
  confidence_score: number;
  affected_vin_count: number;
  most_recent_vin: string;
  detected_at: string;
  created_at: string;
  updated_at: string;
  top_factors?: RootCauseFactor[];
  process_replay_url?: string;
  corrective_action_jira_id: string | null;
}

export interface CorrectiveActionRequest {
  assigned_root_cause_factor_rank: number;
  confirmed_by_user_id: string;
  idempotency_key?: string;
  additional_notes?: string;
}

export interface CorrectiveActionResult {
  jira_ticket_id: string;
  jira_ticket_url: string;
  cluster_id: string;
  created_at: string;
  prepopulated_summary?: string;
}

export interface DefectClusterAlertEvent {
  alert_id: string;
  cluster_id: string;
  defect_class: DefectClass;
  affected_vin_count: number;
  root_cause_signature: string;
  confidence_score: number;
  most_recent_vin: string;
  process_replay_url: string;
  triggered_at: string;
}
