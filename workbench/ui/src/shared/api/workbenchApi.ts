import type {
  EventDetailResponse,
  EventsResponse,
  JobResponse,
  LogsResponse,
  PreviewResponse,
} from './types';

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    ...init,
  });
  const payload = await response.json();
  if (!response.ok) {
    const message = payload?.error?.message || `HTTP ${response.status}`;
    throw new Error(message);
  }
  return payload as T;
}

export function getHealth(): Promise<{ ok: boolean; status: string }> {
  return requestJson('/api/health');
}

export function searchEvents(query: string): Promise<EventsResponse> {
  return requestJson(`/api/events?q=${encodeURIComponent(query)}`);
}

export function getEvent(eventId: string): Promise<EventDetailResponse> {
  return requestJson(`/api/events/${encodeURIComponent(eventId)}`);
}

export type PreviewRequest = {
  event: string;
  domain: string;
  resolution: string;
  mode: string;
  job_id: string;
};

export function previewJob(body: PreviewRequest): Promise<PreviewResponse> {
  return requestJson('/api/jobs/preview', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function startJob(config: Record<string, unknown>): Promise<JobResponse> {
  return requestJson('/api/jobs', {
    method: 'POST',
    body: JSON.stringify({ config, start: true }),
  });
}

export function getJob(jobId: string): Promise<JobResponse> {
  return requestJson(`/api/jobs/${encodeURIComponent(jobId)}`);
}

export function getLogs(jobId: string): Promise<LogsResponse> {
  return requestJson(`/api/jobs/${encodeURIComponent(jobId)}/logs`);
}
