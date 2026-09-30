export type TaskStatus =
  | "SUBMITTED"
  | "ANALYZING"
  | "WAITING_MANUAL_RUN"
  | "WAITING_MANUAL_REVIEW"
  | "REPAIRING"
  | "ADOPTED"
  | "BLOCKED";

export type ObservationCode =
  | "INTERNSHIPS_FOUND"
  | "NO_INTERNSHIPS_OBSERVED"
  | "NO_JOBS_OBSERVED"
  | "NO_JOB_LIST_FOUND"
  | "ACCESS_RESTRICTED"
  | "INCONCLUSIVE";

export interface RepositoryRef {
  name: string;
  role: "source" | "aicoding" | "control";
  baselineRef?: string;
  branch?: string;
  commitSha?: string;
  remoteUrl?: string;
  pushStatus?: "PUSHED" | "DISABLED" | "FAILED";
}

export interface ValidationSummary {
  compile_status?: string;
  pytest_status: string;
  ruff_status: string;
  contract_status: string;
  business_status: string;
}

export interface Report {
  report_id: string;
  task_id: string;
  run_id: string;
  observation_code: ObservationCode;
  technical_status: "PASS" | "PARTIAL" | "FAIL" | "NOT_RUN";
  next_action: string;
  adoptable: boolean;
  report_json: Record<string, unknown> & { validation?: ValidationSummary };
  report_ref?: string | null;
  created_at: string;
}

export interface Task {
  task_id: string;
  batch_id: string;
  entry_url: string;
  normalized_url: string;
  platform_name?: string | null;
  platform_key: string;
  repository_key: string;
  platform_id?: number | null;
  entity_id?: number | null;
  status: TaskStatus;
  next_action?: string | null;
  current_run_id?: string | null;
  last_report_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface BatchSummary {
  batch_id: string;
  client_request_id: string;
  status: string;
  requested_count: number;
  accepted_count: number;
  rejected_count: number;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface BatchDetail {
  batch: BatchSummary;
  tasks: Task[];
}

export interface Submission {
  submission_id: string;
  run_id: string;
  branch_name: string;
  commit_sha?: string | null;
  baseline_ref: string;
  changed_files: string[];
  adoption_status: string;
  simulated: boolean;
}

export interface GlobalSubmission extends Submission {
  task_id: string;
  platform_key: string;
  platform_name?: string | null;
  entry_url: string;
  submission_type?: string;
  created_at: string;
  adopted_at?: string | null;
}

export interface SampleRecord {
  sample_index: number;
  source_id?: string | number | null;
  title?: string | null;
  source_url?: string | null;
  location?: string | null;
  description?: string | null;
  requirements?: string | null;
  publish_time?: string | null;
  department?: string | null;
  employment_type?: string | null;
  job_type?: string | null;
  apply_url?: string | null;
  position?: string | null;
  extra: Record<string, unknown>;
}

export interface SampleBundle {
  manual_run: {
    manual_run_id: string;
    code_revision: string;
    status: string;
    created_at: string;
  } | null;
  count: number;
  samples: SampleRecord[];
}

export interface GlobalSample extends SampleRecord {
  task_id: string;
  platform_key: string;
  platform_name?: string | null;
  entry_url: string;
  manual_run_id: string;
  code_revision: string;
}

export interface PolicyVersion {
  name: string;
  version: string;
  status: string;
  policy: Record<string, unknown>;
  created_by: string;
  created_at: string;
}

export interface RepositoryStatus {
  path: string;
  remote_configured?: boolean;
  remote_url?: string;
  push_enabled?: boolean;
  baseline_ref?: string;
  head?: string | null;
  dirty_files?: string[];
  is_git_repository?: boolean;
  baseline_available?: boolean;
  error?: string;
}

export interface RepositoryStatusBundle {
  control_plane: RepositoryStatus;
  source: RepositoryStatus;
  aicoding: RepositoryStatus;
}

export interface ActorProfile {
  user_id: string;
  role: "viewer" | "operator" | "reviewer" | "admin" | string;
}

export interface SystemHealth {
  status: "ready" | "degraded" | string;
  agent_mode: string;
  analysis_mode: string;
  queue_enabled: boolean;
  checks: Record<string, { status: string; url?: string; detail?: string; name?: string; size_bytes?: number }>;
}

export interface SpecSnapshot {
  spec_version: number;
  schema_version: string;
  spec_hash: string;
  status: string;
  confidence_summary: Record<string, number>;
  spec: Record<string, unknown>;
  evidence_manifest_ref?: string | null;
}

export interface ManualRun {
  manual_run_id: string;
  code_revision: string;
  command_profile: string;
  environment_fingerprint: string;
  started_at: string;
  finished_at: string;
  artifact_manifest_ref: string;
  status: string;
  result?: Record<string, unknown>;
}

export interface TimelineEvent {
  event_id: string;
  event_type: string;
  run_id?: string | null;
  occurred_at: string;
  payload?: Record<string, unknown>;
}

export interface WorkflowRunSummary {
  run_id: string;
  run_type: string;
  attempt: number;
  status: string;
  started_at: string;
  finished_at?: string | null;
  error_code?: string | null;
  error_message?: string | null;
}

export interface TaskTimeline {
  events: TimelineEvent[];
  runs: WorkflowRunSummary[];
}

export interface WorkflowLogRecord {
  log_id: string;
  sequence: number;
  run_id?: string | null;
  stage: string;
  level: string;
  message: string;
  detail: Record<string, unknown>;
  created_at: string;
}

export interface ManualReview {
  review_id: string;
  manual_run_id: string;
  code_revision: string;
  review_status: string;
  reviewer_id: string;
  sample_count: number;
  issue_summary?: string | null;
  field_issues: ReviewFieldIssue[];
  sample_decisions: ReviewSampleDecision[];
  evidence_refs: string[];
  created_at: string;
}

export interface ReviewFieldIssue {
  field: string;
  issue_type: string;
  description: string;
  sample_indices: number[];
  expected?: string | null;
  actual?: string | null;
  code_fixable?: boolean | null;
  evidence_refs: string[];
}

export interface ReviewSampleDecision {
  sample_index: number;
  status: "PASS" | "ISSUE";
  issue_refs: number[];
  note?: string | null;
}

export interface RepairRun {
  repair_run_id: string;
  bundle_id: string;
  attempt: number;
  status: string;
  diagnosis: Record<string, unknown>;
  changed_files: string[];
  regression: Record<string, unknown>;
  commit_sha?: string | null;
  created_at: string;
}

export interface FailureBundle {
  bundle_id: string;
  run_id: string;
  review_id?: string | null;
  failure_type: string;
  code_fixable?: boolean | null;
  status: string;
  bundle: Record<string, unknown>;
  artifact_manifest_ref?: string | null;
  created_at: string;
}

export interface SubmissionDiff {
  submission_id: string;
  available: boolean;
  reason?: string;
  baseline_ref?: string;
  commit_sha?: string;
  changed_files?: string[];
  truncated?: boolean;
  diff: string;
}

export interface TaskBundle {
  task: Task;
  report: Report | null;
  specs: SpecSnapshot[];
  submissions: Submission[];
  evidence: Array<Record<string, unknown>>;
  validation: ValidationSummary | null;
  timeline: TaskTimeline;
  manualRuns: ManualRun[];
  reviews: ManualReview[];
  repairs: RepairRun[];
  failures: FailureBundle[];
  samples: SampleBundle;
  logs: WorkflowLogRecord[];
}

export interface CreateBatchRequest {
  items: Array<{
    entry_url: string;
    platform_name?: string;
    platform_key?: string;
    repository_key?: string;
  }>;
  analysis_profile: string;
  dry_run: boolean;
  client_request_id: string;
}
