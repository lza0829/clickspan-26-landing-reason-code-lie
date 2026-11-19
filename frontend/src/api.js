async function req(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || res.statusText)
  }
  return res.json()
}

export const api = {
  health: () => req('/api/health'),
  overview: () => req('/api/overview'),
  visitors: () => req('/api/visitors'),
  sessions: (visitorId) =>
    req(visitorId ? `/api/sessions?visitor_id=${encodeURIComponent(visitorId)}` : '/api/sessions'),
  session: (id) => req(`/api/sessions/${encodeURIComponent(id)}`),
  quarantine: () => req('/api/quarantine'),
  ingest: (fixture) =>
    req('/api/ingest/run', {
      method: 'POST',
      body: JSON.stringify(fixture ? { fixture } : {}),
    }),
  proofs: (sessionId) =>
    req(sessionId ? `/api/session-proofs?session_id=${encodeURIComponent(sessionId)}` : '/api/session-proofs'),
  settings: () => req('/api/settings'),
  landingRules: () => req('/api/landing/rules'),
  saveLandingRules: (eventNames, note) =>
    req('/api/landing/rules', {
      method: 'PUT',
      body: JSON.stringify({ event_names: eventNames, note: note || '' }),
    }),
  landingSplits: () => req('/api/landing/splits'),
  landingVerify: () => req('/api/landing/verify'),
}
