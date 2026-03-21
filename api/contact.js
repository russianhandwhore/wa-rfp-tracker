// api/contact.js
// Vercel serverless function — API key stored server-side only, never exposed to browser.
// Rate limited per IP: max 10 requests per hour per visitor.

// In-memory rate limit store (resets when function cold-starts, good enough for abuse prevention)
const rateLimitMap = new Map()
const MAX_REQUESTS_PER_HOUR = 10
const ONE_HOUR_MS = 60 * 60 * 1000

function isRateLimited(ip) {
  const now = Date.now()
  const record = rateLimitMap.get(ip) || { count: 0, windowStart: now }

  // Reset window if an hour has passed
  if (now - record.windowStart > ONE_HOUR_MS) {
    record.count = 0
    record.windowStart = now
  }

  record.count++
  rateLimitMap.set(ip, record)

  return record.count > MAX_REQUESTS_PER_HOUR
}

export default async function handler(req, res) {
  // Only allow POST
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' })
  }

  // Only allow requests from your own domain (blocks direct API abuse)
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

  // Validate input
  const { name, agency } = req.body
  if (!name || typeof name !== 'string' || name.length > 100) {
    return res.status(400).json({ error: 'Invalid input' })
  }

  // Sanitize name — must look like a real person name
  const cleanName = name.trim()
  if (!/^[a-zA-Z\s\-\.\']{2,60}$/.test(cleanName)) {
    return res.status(400).json({ error: 'Invalid name format' })
  }

  // Sanitize agency — strip any characters that could inject into the prompt
  const cleanAgency = (agency || '').toString().replace(/[^a-zA-Z0-9\s\-\.\,&()]/g, '').slice(0, 100).trim()

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
        max_tokens: 200,  // Keep low to limit cost per call (~$0.0001)
        messages: [{
          role: 'user',
          content: `Find the work email and phone number for "${cleanName}" who works in government procurement at "${cleanAgency}" in Washington State. Return ONLY valid JSON: {"email": null, "phone": null, "title": null}. Use null for unknown fields. Do not guess or fabricate.`
        }]
      })
    })

    if (!response.ok) {
      return res.status(500).json({ error: 'Lookup service unavailable' })
    }

    const data = await response.json()
    const text = data.content?.[0]?.text || '{}'
    const clean = text.replace(/```json|```/g, '').trim()

    // Extract JSON even if Claude wraps it in extra text
    const jsonMatch = clean.match(/\{[\s\S]*\}/)
    if (!jsonMatch) {
      return res.status(200).json({ email: null, phone: null, title: null })
    }

    const parsed = JSON.parse(jsonMatch[0])

    // Sanitize output — only return expected fields
    return res.status(200).json({
      email: typeof parsed.email === 'string' && parsed.email !== 'null' ? parsed.email : null,
      phone: typeof parsed.phone === 'string' && parsed.phone !== 'null' ? parsed.phone : null,
      title: typeof parsed.title === 'string' && parsed.title !== 'null' ? parsed.title : null,
    })

  } catch (e) {
    return res.status(500).json({ error: 'Lookup failed' })
  }
}
