// api/contact.js
// Vercel serverless function — API key stored server-side only, never exposed to browser.
// Rate limited per IP: max 10 requests per hour per visitor.

const rateLimitMap = new Map()
const MAX_REQUESTS_PER_HOUR = 10
const ONE_HOUR_MS = 60 * 60 * 1000

function isRateLimited(ip) {
  const now = Date.now()
  const record = rateLimitMap.get(ip) || { count: 0, windowStart: now }
  if (now - record.windowStart > ONE_HOUR_MS) {
    record.count = 0
    record.windowStart = now
  }
  record.count++
  rateLimitMap.set(ip, record)
  return record.count > MAX_REQUESTS_PER_HOUR
}

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' })
  }

  // Only allow requests from your own domain
  const origin = req.headers.origin || ''
  const referer = req.headers.referer || ''
  const allowed = origin.includes('vercel.app') || origin.includes('wa-rfp-tracker') ||
                  referer.includes('vercel.app') || referer.includes('wa-rfp-tracker')
  if (!allowed && process.env.NODE_ENV === 'production') {
    return res.status(403).json({ error: 'Forbidden' })
  }

  // Rate limit by IP
  const ip = req.headers['x-forwarded-for']?.split(',')[0]?.trim() || req.socket?.remoteAddress || 'unknown'
  if (isRateLimited(ip)) {
    return res.status(429).json({ error: 'Too many requests. Please wait before trying again.' })
  }

  const { name, agency, department } = req.body

  if (!name || typeof name !== 'string' || name.length > 100) {
    return res.status(400).json({ error: 'Invalid input' })
  }

  const cleanName = name.trim()
  if (!/^[a-zA-Z\s\-\.\']{2,60}$/.test(cleanName)) {
    return res.status(400).json({ error: 'Invalid name format' })
  }

  // Use department if available, otherwise agency — never use the platform name
  const rawOrg = (department || agency || '').toString()
  // Strip platform names that leak through
  const platformNames = ['WEBS', 'Washington Electronic Business Solution', 'OpenGov', 'Procureware']
  let cleanOrg = rawOrg
  for (const p of platformNames) {
    cleanOrg = cleanOrg.replace(new RegExp(p, 'gi'), '').trim()
  }
  cleanOrg = cleanOrg.replace(/[^a-zA-Z0-9\s\-\.\,&()]/g, '').replace(/^[\s\-]+|[\s\-]+$/g, '').slice(0, 100)

  // Build a useful search context
  const orgContext = cleanOrg.length > 2 ? `at "${cleanOrg}"` : 'in Washington State government procurement'

  try {
    const response = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': process.env.ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01',
      },
      body: JSON.stringify({
        model: 'claude-haiku-4-5-20251001',
        max_tokens: 600,
        tools: [{ type: 'web_search_20250305', name: 'web_search' }],
        messages: [{
          role: 'user',
          content: `Search the web for "${cleanName}" ${orgContext} in Washington State. Find their official work email address, phone number, and job title from government websites, LinkedIn, or official directories. Return ONLY this JSON with no other text: {"email": null, "phone": null, "title": null}. Use null for fields you cannot confirm with a real source.`
        }]
      })
    })

    if (!response.ok) {
      return res.status(500).json({ error: 'Lookup service unavailable' })
    }

    const data = await response.json()

    // Web search causes multi-turn: find the last text block after tool use
    const textBlock = (data.content || []).filter(b => b.type === 'text').pop()
    const text = textBlock?.text || '{}'
    const clean = text.replace(/```json|```/g, '').trim()

    const jsonMatch = clean.match(/\{[\s\S]*\}/)
    if (!jsonMatch) {
      return res.status(200).json({ email: null, phone: null, title: null })
    }

    const parsed = JSON.parse(jsonMatch[0])

    return res.status(200).json({
      email: typeof parsed.email === 'string' && parsed.email !== 'null' ? parsed.email.trim() : null,
      phone: typeof parsed.phone === 'string' && parsed.phone !== 'null' ? parsed.phone.trim() : null,
      title: typeof parsed.title === 'string' && parsed.title !== 'null' ? parsed.title.trim() : null,
    })

  } catch (e) {
    return res.status(500).json({ error: 'Lookup failed' })
  }
}
