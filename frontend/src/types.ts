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

export interface TaskBundle {
  task: Task;
  report: Report | null;
  specs: SpecSnapshot[];
  submissions: Submission[];
  evidence: Array<Record<string, unknown>>;
  validation: ValidationSummary | null;
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
