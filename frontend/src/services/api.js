// API Client and Voice Audio Helpers

let rawBase = (import.meta.env.VITE_API_URL || "").replace(/\/+$/, "")
// If running in browser:
// 1. On localhost or 127.0.0.1, always use relative path "" (Vite dev proxy forwards directly to local 127.0.0.1:8000)
// 2. On Vercel (or when pointing to railway), always use relative path "" (vercel.json rewrites /api/* to local tunnel)
if (typeof window !== "undefined") {
  if (
    window.location.hostname === "localhost" ||
    window.location.hostname === "127.0.0.1" ||
    window.location.hostname.includes("vercel.app") ||
    rawBase.includes("railway.app")
  ) {
    rawBase = ""
  }
}
const BASE_URL = rawBase

export async function startChat({ message, threadId, headless = true }) {
  const res = await fetch(`${BASE_URL}/api/chat/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      thread_id: threadId,
      headless,
    }),
  })
  if (!res.ok) {
    const error = await res.json().catch(() => ({}))
    throw new Error(error.detail || `Failed to start chat: ${res.statusText}`)
  }
  return res.json()
}

export async function replyChat({ threadId, answer }) {
  const res = await fetch(`${BASE_URL}/api/chat/reply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      thread_id: threadId,
      answer,
    }),
  })
  if (!res.ok) {
    const error = await res.json().catch(() => ({}))
    throw new Error(error.detail || `Failed to send reply: ${res.statusText}`)
  }
  return res.json()
}

export async function resetSession(threadId) {
  const res = await fetch(`${BASE_URL}/api/reset`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ thread_id: threadId }),
  })
  return res.json().catch(() => ({}))
}

export async function cancelChat({ threadId }) {
  const res = await fetch(`${BASE_URL}/api/chat/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ thread_id: threadId }),
  })
  return res.json().catch(() => ({}))
}

export async function getSessionHistory(threadId, after = 0) {
  try {
    const res = await fetch(`${BASE_URL}/api/session/${threadId}/history?after=${after}`)
    if (!res.ok) return { events: [], total: 0 }
    return await res.json()
  } catch (err) {
    return { events: [], total: 0 }
  }
}

export async function uploadAudioForTranscription(audioBlob, context = "") {
  const formData = new FormData()
  formData.append("file", audioBlob, "speech.webm")
  formData.append("context", context)

  const res = await fetch(`${BASE_URL}/api/transcribe`, {
    method: "POST",
    body: formData,
  })
  if (!res.ok) {
    const error = await res.json().catch(() => ({}))
    throw new Error(error.detail || "Audio transcription failed")
  }
  return res.json()
}

export function subscribeToEvents(threadId, onMessage, onError, onOpen) {
  const url = `${BASE_URL}/api/session/${threadId}/events`
  const es = new EventSource(url)

  es.onopen = () => {
    if (onOpen) onOpen()
  }

  es.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      onMessage(data)
    } catch (e) {
      console.warn("Failed to parse SSE payload", event.data, e)
    }
  }

  es.onerror = (err) => {
    if (onError) onError(err)
  }

  return () => {
    es.close()
  }
}

// -----------------------------------------------------------------
// Performance Analytics & Benchmarking API Client
// -----------------------------------------------------------------

function buildQueryString(params) {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== undefined && v !== null && v !== "") {
      q.append(k, String(v))
    }
  }
  const s = q.toString()
  return s ? `?${s}` : ""
}

export async function fetchMetricsOverview(params = {}) {
  const qs = buildQueryString({
    time_range: params.timeRange || "24h",
    include_benchmark: Boolean(params.includeBenchmark),
    start_date: params.startDate,
    end_date: params.endDate,
  })
  const res = await fetch(`${BASE_URL}/api/metrics/overview${qs}`)
  if (!res.ok) throw new Error(`Overview fetch failed: ${res.statusText}`)
  return res.json()
}

export async function fetchMetricsLatency(params = {}) {
  const qs = buildQueryString({
    time_range: params.timeRange || "24h",
    include_benchmark: Boolean(params.includeBenchmark),
    start_date: params.startDate,
    end_date: params.endDate,
  })
  const res = await fetch(`${BASE_URL}/api/metrics/latency${qs}`)
  if (!res.ok) throw new Error(`Latency fetch failed: ${res.statusText}`)
  return res.json()
}

export async function fetchMetricsSuppliers(params = {}) {
  const qs = buildQueryString({
    time_range: params.timeRange || "24h",
    include_benchmark: Boolean(params.includeBenchmark),
    start_date: params.startDate,
    end_date: params.endDate,
  })
  const res = await fetch(`${BASE_URL}/api/metrics/suppliers${qs}`)
  if (!res.ok) throw new Error(`Suppliers fetch failed: ${res.statusText}`)
  return res.json()
}

export async function fetchMetricsQuality(params = {}) {
  const qs = buildQueryString({
    time_range: params.timeRange || "24h",
    include_benchmark: Boolean(params.includeBenchmark),
    start_date: params.startDate,
    end_date: params.endDate,
  })
  const res = await fetch(`${BASE_URL}/api/metrics/quality${qs}`)
  if (!res.ok) throw new Error(`Quality fetch failed: ${res.statusText}`)
  return res.json()
}

export async function fetchMetricsHistory(params = {}) {
  const qs = buildQueryString({
    time_range: params.timeRange || "24h",
    granularity: params.granularity || "hour",
    include_benchmark: Boolean(params.includeBenchmark),
    start_date: params.startDate,
    end_date: params.endDate,
  })
  const res = await fetch(`${BASE_URL}/api/metrics/history${qs}`)
  if (!res.ok) throw new Error(`History fetch failed: ${res.statusText}`)
  return res.json()
}

export async function fetchMetricsRequests(params = {}) {
  const qs = buildQueryString({
    limit: params.limit || 50,
    offset: params.offset || 0,
    query: params.query,
    status: params.status,
    time_range: params.timeRange || "24h",
    include_benchmark: Boolean(params.includeBenchmark),
  })
  const res = await fetch(`${BASE_URL}/api/metrics/requests${qs}`)
  if (!res.ok) throw new Error(`Requests fetch failed: ${res.statusText}`)
  return res.json()
}

export async function fetchRequestDetail(requestId) {
  const res = await fetch(`${BASE_URL}/api/metrics/requests/${encodeURIComponent(requestId)}`)
  if (!res.ok) throw new Error(`Request detail fetch failed: ${res.statusText}`)
  return res.json()
}

export async function clearMetricsData({ includeBenchmark = true } = {}) {
  const qs = buildQueryString({ include_benchmark: includeBenchmark })
  const res = await fetch(`${BASE_URL}/api/metrics/clear${qs}`, {
    method: "POST",
  })
  if (!res.ok) {
    const error = await res.json().catch(() => ({}))
    throw new Error(error.detail || `Clear metrics failed: ${res.statusText}`)
  }
  return res.json()
}

export async function runBenchmark({ concurrency = 1, mode = "sequential", queries = null, timeoutSeconds = 35.0 }) {
  const res = await fetch(`${BASE_URL}/api/benchmark/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      concurrency,
      mode,
      queries,
      timeout_seconds: timeoutSeconds,
    }),
  })
  if (!res.ok) {
    const error = await res.json().catch(() => ({}))
    throw new Error(error.detail || `Benchmark failed: ${res.statusText}`)
  }
  return res.json()
}

export async function fetchBenchmarkStatus() {
  const res = await fetch(`${BASE_URL}/api/benchmark/status`)
  if (!res.ok) throw new Error(`Benchmark status fetch failed: ${res.statusText}`)
  return res.json()
}

export async function cancelBenchmark() {
  const res = await fetch(`${BASE_URL}/api/benchmark/cancel`, {
    method: "POST",
  })
  return res.json().catch(() => ({}))
}
