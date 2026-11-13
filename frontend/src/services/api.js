// API Client and Voice Audio Helpers

let rawBase = (import.meta.env.VITE_API_URL || "").replace(/\/+$/, "")
// If pointing to Railway, route through relative /api proxy (which vercel.json rewrites to the local tunnel)
if (rawBase.includes("railway.app")) {
  rawBase = ""
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
