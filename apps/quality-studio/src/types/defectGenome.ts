export type DefectClass = "dust" | "orange_peel" | "run" | "crater" | "pinhole";
export type ClusterStatus = "open" | "in_progress" | "resolved";
export type FactorCategory = "prep_stage" | "booth_parameter" | "climate_condition";

export interface RootCauseFactor {
  rank: number;
  factor_name: string;
  factor_category: FactorCategory;
  probability_score: number;
  supporting_vin_count: number;
}

export interface AdvisorV1Result {
  rule_id: string;
  rule_description: string;
  matched_conditions?: string[];
  recommended_action: string;
}

export interface ClusterQueryResult {
  query_id: string;
  cluster_id: string;
  queried_at: string;
  defect_class: DefectClass;
  model_confidence: number;
  low_confidence: boolean;
  top_factors: RootCauseFactor[];
  affected_vin_count: number;
  affected_vins?: string[];
  model_version?: string;
  advisor_v1_fallback?: AdvisorV1Result | null;
}

export interface ClusterQueryRequest {
  defect_class: DefectClass;
  vin_filter?: string[];
  time_window_hours?: number;
  idempotency_key?: string;
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
  corrective_action_jira_id?: string | null;
}

export interface CursorPage {
  next_cursor: string | null;
  limit: number;
}

export interface ClusterListResponse {
  data: DefectCluster[];
  page: CursorPage;
}

export interface ConfidenceThresholdConfig {
  low_confidence_threshold: number;
  cluster_alert_threshold: number;
  cluster_alert_min_vins?: number;
  cluster_alert_window_hours?: number;
  updated_at?: string | null;
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

export interface ModelFeedbackRequest {
  cluster_id: string;
  query_id: string;
  engineer_user_id: string;
  accepted_factor_rank: number;
  defect_class: DefectClass;
  notes?: string;
  override_root_cause?: string;
}

export interface ModelFeedbackAck {
  feedback_id: string;
  accepted_at: string;
}

export interface ApiError {
  code: string;
  message: string;
  correlation_id: string;
  details?: Record<string, unknown>[];
}
