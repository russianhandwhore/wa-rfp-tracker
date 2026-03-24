// api/contact.js
// Vercel serverless function — API key stored server-side only.
// Rate limited per IP: max 10 requests per hour.

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

function extractJson(text) {
  if (!text) return null
  const clean = text.replace(/```json|```/g, '').trim()
  const match = clean.match(/\{[\s\S]*\}/)
  if (!match) return null
  try { return JSON.parse(match[0]) } catch { return null }
}

function safeStr(val) {
  return typeof val === 'string' && val !== 'null' && val.trim().length > 0
    ? val.trim()
    : null
}

async function callClaude(messages, apiKey) {
  const res = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01',
    },
    body: JSON.stringify({
      model: 'claude-sonnet-4-6',
      max_tokens: 2048,
      tools: [{ type: 'web_search_20250305', name: 'web_search' }],
      messages,
    })
  })
  if (!res.ok) throw new Error(`API error ${res.status}`)
  return res.json()
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

  const isEmail = /^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$/.test(cleanName)
  const isName  = /^[a-zA-Z0-9\s\-\.'@._+]{2,80}$/.test(cleanName)
  if (!isEmail && !isName) {
    return res.status(400).json({ error: 'Invalid name format' })
  }

  // Strip platform names from org
  const platformNames = ['WEBS', 'Washington Electronic Business Solution', 'OpenGov', 'Procureware', 'OMWBE']
  let rawOrg = (department || agency || '').toString()
  for (const p of platformNames) rawOrg = rawOrg.replace(new RegExp(p, 'gi'), '').trim()
  const cleanOrg = rawOrg.replace(/[^a-zA-Z0-9\s\-\.\,&()]/g, '').replace(/^[\s\-]+|[\s\-]+$/g, '').slice(0, 100)

  // Search term — no quotes, matches Google button exactly
  const searchTerm = `${cleanName} ${cleanOrg} Washington`.trim()

  const prompt = `Search the web for: ${searchTerm}

Read the search result snippets. Extract any phone number, email, job title, and full name you find that belongs to this person.

Reply with ONLY this JSON — no other text:
{"name": null, "title": null, "phone": null, "email": null}

Fill in what you find. Use null only if it is genuinely absent from the results.`

  try {
    const apiKey = process.env.ANTHROPIC_API_KEY

    // Turn 1: send prompt
    const messages = [{ role: 'user', content: prompt }]
    let data = await callClaude(messages, apiKey)

    // Turn 2: if Claude stopped to use the tool, complete the tool call and get final answer
    if (data.stop_reason === 'tool_use') {
      const assistantContent = data.content
      const toolUseBlock = assistantContent.find(b => b.type === 'tool_use')

      if (toolUseBlock) {
        messages.push({ role: 'assistant', content: assistantContent })
        messages.push({
          role: 'user',
          content: [{
            type: 'tool_result',
            tool_use_id: toolUseBlock.id,
            content: 'Search complete. Now extract the contact details and return the JSON.'
          }]
        })
        data = await callClaude(messages, apiKey)
      }
    }

    // Parse the final text block
    const textBlocks = (data.content || []).filter(b => b.type === 'text')
    const lastText = textBlocks[textBlocks.length - 1]?.text || ''
    const parsed = extractJson(lastText)

    if (!parsed) {
      return res.status(200).json({ name: null, email: null, phone: null, title: null })
    }

    return res.status(200).json({
      name:  safeStr(parsed.name),
      email: safeStr(parsed.email),
      phone: safeStr(parsed.phone),
      title: safeStr(parsed.title),
    })

  } catch (e) {
    return res.status(500).json({ error: 'Lookup failed' })
  }
}
