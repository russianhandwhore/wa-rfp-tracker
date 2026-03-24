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

  const origin = req.headers.origin || ''
  const referer = req.headers.referer || ''
  const allowed = origin.includes('vercel.app') || origin.includes('wa-rfp-tracker') ||
                  referer.includes('vercel.app') || referer.includes('wa-rfp-tracker')
  if (!allowed && process.env.NODE_ENV === 'production') {
    return res.status(403).json({ error: 'Forbidden' })
  }

  const ip = req.headers['x-forwarded-for']?.split(',')[0]?.trim() || req.socket?.remoteAddress || 'unknown'
  if (isRateLimited(ip)) {
    return res.status(429).json({ error: 'Too many requests. Please wait before trying again.' })
  }

  const { name, agency, department } = req.body

  if (!name || typeof name !== 'string' || name.length > 120) {
    return res.status(400).json({ error: 'Invalid input' })
  }

  const cleanName = name.trim()

  // Accept either a person name OR an email address as the search term
  const isEmail = /^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$/.test(cleanName)
  const isName = /^[a-zA-Z\s\-\.'@.]{2,80}$/.test(cleanName)

  if (!isEmail && !isName) {
    return res.status(400).json({ error: 'Invalid name format' })
  }

  // Build org context — strip platform names that may leak through
  const platformNames = ['WEBS', 'Washington Electronic Business Solution', 'OpenGov', 'Procureware', 'OMWBE']
  let rawOrg = ((department || agency || '')).toString()
  for (const p of platformNames) {
    rawOrg = rawOrg.replace(new RegExp(p, 'gi'), '').trim()
  }
  const cleanOrg = rawOrg.replace(/[^a-zA-Z0-9\s\-\.\,&()]/g, '').replace(/^[\s\-]+|[\s\-]+$/g, '').slice(0, 100)
  const orgContext = cleanOrg.length > 2 ? `at "${cleanOrg}"` : 'in Washington State government procurement'

  // Build search prompt — aggressive, multi-search, extract everything found
  const searchPrompt = isEmail
    ? `You are a research assistant. The email address "${cleanName}" belongs to a government procurement contact ${orgContext} in Washington State.

Search the web to identify this person. Try searches like:
- "${cleanName}"
- The email domain staff directory
- "${cleanName.split('@')[1]}" staff directory procurement

From the search results, extract:
- Their full name
- Their job title / role
- Their direct phone number or department phone
- Confirm their email

Return ONLY valid JSON with no extra text:
{"name": null, "title": null, "phone": null, "email": "${cleanName}"}

Fill every field you can find from search results. Only use null if you truly cannot find it.`

    : `You are a research assistant. Search the web to find contact details for "${cleanName}" ${orgContext} in Washington State.

Try multiple searches:
- "${cleanName}" "${cleanOrg}"
- "${cleanName}" Washington State government phone email
- "${cleanOrg}" staff directory "${cleanName}"

From the search results, extract:
- Their job title / role
- Their direct work email address
- Their direct phone number or department phone

Return ONLY valid JSON with no extra text:
{"title": null, "email": null, "phone": null}

Fill every field you can find. Only use null if you truly cannot find it after searching.`

  try {
    const response = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': process.env.ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01',
      },
      body: JSON.stringify({
        model: 'claude-sonnet-4-20250514',
        max_tokens: 1024,
        tools: [{ type: 'web_search_20250305', name: 'web_search' }],
        messages: [{ role: 'user', content: searchPrompt }]
      })
    })

    if (!response.ok) {
      return res.status(500).json({ error: 'Lookup service unavailable' })
    }

    const data = await response.json()
    // Extract the final text block — comes after tool use blocks
    const textBlock = (data.content || []).filter(b => b.type === 'text').pop()
    const text = textBlock?.text || '{}'
    const clean = text.replace(/```json|```/g, '').trim()

    const jsonMatch = clean.match(/\{[\s\S]*\}/)
    if (!jsonMatch) {
      return res.status(200).json({ email: null, phone: null, title: null, name: null })
    }

    const parsed = JSON.parse(jsonMatch[0])

    return res.status(200).json({
      name:  typeof parsed.name  === 'string' && parsed.name  !== 'null' ? parsed.name.trim()  : null,
      email: typeof parsed.email === 'string' && parsed.email !== 'null' ? parsed.email.trim() : null,
      phone: typeof parsed.phone === 'string' && parsed.phone !== 'null' ? parsed.phone.trim() : null,
      title: typeof parsed.title === 'string' && parsed.title !== 'null' ? parsed.title.trim() : null,
    })

  } catch (e) {
    return res.status(500).json({ error: 'Lookup failed' })
  }
}
