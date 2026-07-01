export type Period = {
  start?: string;
  end?: string;
};

export type WorkbenchEvent = {
  id: string;
  name?: string;
  description?: string;
  event_type?: string;
  start?: string;
  end?: string;
  period?: Period;
  default_domain?: string;
  default_resolution_preset?: string;
  suggested_outputs?: string[];
};

export type DomainPreset = {
  id: string;
  label?: string;
  center_lat: number;
  center_lon: number;
  dx_km: number;
  dy_km: number;
  e_we: number;
  e_sn: number;
};

export type ResolutionPreset = {
  id: string;
  label?: string;
};

export type EventDetailResponse = {
  ok: boolean;
  event: WorkbenchEvent;
  domain_presets: DomainPreset[];
  resolution_presets: ResolutionPreset[];
};

export type EventsResponse = {
  ok: boolean;
  count: number;
  events: WorkbenchEvent[];
};

export type PreviewResponse = {
  ok: boolean;
  valid: boolean;
  errors: string[];
  config: Record<string, unknown>;
};

export type JobFile = {
  name: string;
  relative_path: string;
  size_bytes: number;
  text?: string;
};

export type JobSummary = {
  job_id: string;
  run_dir?: string;
  status?: {
    status?: string;
    state?: string;
  };
  logs?: JobFile[];
  outputs?: JobFile[];
};

export type JobResponse = {
  ok: boolean;
  job: JobSummary;
};

export type LogsResponse = {
  ok: boolean;
  job_id: string;
  logs: JobFile[];
};
