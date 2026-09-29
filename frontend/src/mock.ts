import type { Report, SpecSnapshot, Submission, Task, TaskBundle } from "./types";

const now = new Date().toISOString();

export const mockTasks: Task[] = [
  {
    task_id: "task-real-kuaishou-001",
    batch_id: "batch-real-kuaishou-001",
    entry_url: "https://campus.kuaishou.cn/#/campus/jobs?recruitSubProjectCodes=20271772783534",
    normalized_url: "https://campus.kuaishou.cn/#/campus/jobs?recruitSubProjectCodes=20271772783534",
    platform_name: "快手",
    platform_key: "kuaishou",
    repository_key: "aicoding-auto_spider",
    platform_id: null,
    entity_id: null,
    status: "WAITING_MANUAL_RUN",
    next_action: "WAIT_MANUAL_RUN",
    current_run_id: "run-real-kuaishou-repair",
    last_report_id: "report-real-kuaishou-003",
    created_at: "2026-09-29T09:31:03Z",
    updated_at: now,
  },
  {
    task_id: "task-lilith-preview-002",
    batch_id: "batch-preview-002",
    entry_url: "https://lilithgames.jobs.feishu.cn/intern/",
    normalized_url: "https://lilithgames.jobs.feishu.cn/intern/",
    platform_name: "莉莉丝游戏",
    platform_key: "lilith_games",
    repository_key: "aicoding-auto_spider",
    platform_id: null,
    entity_id: null,
    status: "ANALYZING",
    next_action: "WAIT_ANALYSIS",
    current_run_id: "run-lilith-002",
    last_report_id: null,
    created_at: "2026-09-29T08:12:11Z",
    updated_at: "2026-09-29T08:19:31Z",
  },
  {
    task_id: "task-moka-review-003",
    batch_id: "batch-review-003",
    entry_url: "https://app.mokahr.com/campus-recruitment/",
    normalized_url: "https://app.mokahr.com/campus-recruitment/",
    platform_name: "三七互娱",
    platform_key: "sanqi",
    repository_key: "aicoding-auto_spider",
    platform_id: null,
    entity_id: null,
    status: "WAITING_MANUAL_REVIEW",
    next_action: "WAIT_REVIEW",
    current_run_id: "run-moka-003",
    last_report_id: "report-moka-003",
    created_at: "2026-09-28T17:44:08Z",
    updated_at: "2026-09-29T07:53:20Z",
  },
];

const validation = {
  compile_status: "PASS",
  pytest_status: "PASS",
  ruff_status: "PASS",
  contract_status: "PASS",
  business_status: "PASS",
};

const submissions: Submission[] = [
  {
    submission_id: "sub-real-kuaishou-output-001",
    run_id: "run-output-kuaishou-001",
    branch_name: "ai/onboarding/kuaishou/output-kuaishou-001",
    commit_sha: "e2159d7853542380ae0f80ae1a3220fd6a1bc139",
    baseline_ref: "16cba8439396e371973e4ee0301d3a88f2f32ba5",
    changed_files: [
      "collectors/kuaishou.py",
      "config/platforms/kuaishou.toml",
      "tests/test_kuaishou.py",
      "tests/fixtures/kuaishou/*",
    ],
    adoption_status: "candidate",
    simulated: false,
  },
  {
    submission_id: "sub-real-kuaishou-repair-001",
    run_id: "run-real-kuaishou-repair",
    branch_name: "ai/repair/kuaishou/task-real-kuaishou-001",
    commit_sha: "323a10eca77ff278396426bd13e70303c7906e7f",
    baseline_ref: "e2159d7853542380ae0f80ae1a3220fd6a1bc139",
    changed_files: ["collectors/kuaishou.py", "tests/test_kuaishou.py"],
    adoption_status: "candidate",
    simulated: false,
  },
];

export const mockReport: Report = {
  report_id: "report-real-kuaishou-003",
  task_id: "task-real-kuaishou-001",
  run_id: "run-real-kuaishou-repair",
  observation_code: "INTERNSHIPS_FOUND",
  technical_status: "PASS",
  next_action: "WAIT_MANUAL_RUN",
  adoptable: true,
  report_json: {
    validation,
    list_count: 227,
    internship_count: 2,
    valid_record_count: 2,
    platform_id: null,
    entity_id: null,
  },
  report_ref: "task-real-kuaishou-001/run-real-kuaishou-repair/repair-validation.json",
  created_at: now,
};

const mockSpec: SpecSnapshot = {
  spec_version: 1,
  schema_version: "1.0",
  spec_hash: "d7d1dff817e6fc8ad898f7ba9c79ac23ea5fb433707a6713ac858c98a4c98c8f",
  status: "CANDIDATE",
  confidence_summary: { high: 3, medium: 0, low: 0 },
  spec: {
    platform_key: "kuaishou",
    adapter: { family: "custom_http", runtime_mode: "http" },
    endpoints: {
      list: { method: "POST", path: "/recruit/campus/e/api/v1/open/positions/simple" },
      detail: { method: "GET", path: "/recruit/campus/e/api/v1/open/positions/find" },
    },
    pagination: { mode: "page", page_size: 10, max_pages: 23 },
  },
};

export const mockBundle: TaskBundle = {
  task: mockTasks[0],
  report: mockReport,
  specs: [mockSpec],
  submissions,
  evidence: [
    { evidence_id: "819bf3d7bf8344b61a010e145ea881e7", file_type: "candidate_validation", size_bytes: 1738 },
    { evidence_id: "5ca5fa8985320f399668a08607865eaf", file_type: "manual_run", size_bytes: 5146 },
    { evidence_id: "b8a3099c83cfe85e2f9ee8d4dd6a831f", file_type: "repair_validation", size_bytes: 517 },
  ],
  validation,
  timeline: {
    events: [
      { event_id: "event-1", event_type: "TASK_CREATED", occurred_at: "2026-09-29T09:31:03Z", payload: {} },
      { event_id: "event-2", event_type: "REPORT_CREATED", occurred_at: "2026-09-29T09:35:03Z", payload: { observation_code: "INTERNSHIPS_FOUND" } },
    ],
    runs: [
      { run_id: "run-real-kuaishou-repair", run_type: "onboarding", attempt: 1, status: "COMPLETED", started_at: "2026-09-29T09:31:03Z", finished_at: "2026-09-29T09:35:03Z" },
    ],
  },
  manualRuns: [],
  reviews: [],
  repairs: [],
  failures: [],
  samples: {
    manual_run: null,
    count: 2,
    samples: [
      {
        sample_index: 1,
        source_id: "11301",
        title: "【留用实习】策略产品经理-商业化方向",
        source_url: "https://campus.example.com/jobs/11301",
        location: "北京",
        description: "负责产品分析与方案设计。",
        requirements: "每周实习四天。",
        extra: {},
      },
      {
        sample_index: 2,
        source_id: "11302",
        title: "【留用实习】策略运营-分析",
        source_url: "https://campus.example.com/jobs/11302",
        location: "北京",
        description: "参与业务数据分析。",
        requirements: "熟悉常用分析工具。",
        extra: {},
      },
    ],
  },
};
