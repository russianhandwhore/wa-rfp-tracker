import { useState, useEffect } from 'react'
import { createClient } from '@supabase/supabase-js'

const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL,
  import.meta.env.VITE_SUPABASE_ANON_KEY
)

const PLATFORMS = ['All', 'WEBS', 'OpenGov', 'Procureware', 'PublicPurchase', 'SAP_Ariba', 'Oracle', 'Bonfire', 'Workday', 'Biddingo', 'Standalone']
const PER_PAGE = 25

export default function App() {
  const [rfps, setRfps] = useState([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [platform, setPlatform] = useState('All')
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)

  useEffect(() => {
    fetchRfps()
  }, [search, platform, page])

  async function fetchRfps() {
    setLoading(true)
    let query = supabase
      .from('rfps')
      .select('*', { count: 'exact' })
      .eq('status', 'active')
      .order('due_date', { ascending: true })
      .range((page - 1) * PER_PAGE, page * PER_PAGE - 1)

    if (search) {
      query = query.ilike('title', '%' + search + '%')
    }
    if (platform !== 'All') {
      query = query.eq('source_platform', platform)
    }

    const { data, count, error } = await query
    if (!error) {
      setRfps(data || [])
      setTotal(count || 0)
    }
    setLoading(false)
  }

  function formatDate(dateStr) {
    if (!dateStr) return 'No date'
    return new Date(dateStr).toLocaleDateString('en-US', {
      month: 'short', day: 'numeric', year: 'numeric'
    })
  }

  function getDaysLeft(dateStr) {
    if (!dateStr) return null
    return Math.ceil((new Date(dateStr) - new Date()) / (1000 * 60 * 60 * 24))
  }

  function getDaysColor(days) {
    if (days === null || days < 0) return 'text-gray-400'
    if (days <= 3) return 'text-red-500 font-bold'
    if (days <= 7) return 'text-orange-500 font-semibold'
    return 'text-green-600'
  }

  const totalPages = Math.ceil(total / PER_PAGE)

  return (
    <div className="min-h-screen bg-gray-50">
      <header style={{ backgroundColor: '#151515' }}>
        <div className="max-w-7xl mx-auto px-4 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div style={{ backgroundColor: '#EE0000' }} className="w-8 h-8 rounded flex items-center justify-center">
              <span className="text-white font-bold text-sm">WA</span>
            </div>
            <div>
              <h1 className="text-white font-bold text-lg leading-tight">Washington State RFP Tracker</h1>
              <p className="text-gray-400 text-xs">Active procurement opportunities across all state agencies</p>
            </div>
          </div>
          <div className="text-gray-400 text-sm">{total} active RFPs</div>
        </div>
      </header>

      <div style={{ backgroundColor: '#212427' }} className="border-b border-gray-700">
        <div className="max-w-7xl mx-auto px-4 py-4 flex flex-col md:flex-row gap-3">
          <div className="flex-1">
            <input
              type="text"
              placeholder="Search RFPs by title..."
              value={search}
              onChange={e => { setSearch(e.target.value); setPage(1) }}
              className="w-full px-4 py-2 rounded bg-gray-800 text-white border border-gray-600 focus:outline-none focus:border-red-500 placeholder-gray-500"
            />
          </div>
          <select
            value={platform}
            onChange={e => { setPlatform(e.target.value); setPage(1) }}
            className="px-4 py-2 rounded bg-gray-800 text-white border border-gray-600 focus:outline-none focus:border-red-500"
          >
            {PLATFORMS.map(p => (
              <option key={p} value={p}>{p === 'All' ? 'All Platforms' : p}</option>
            ))}
          </select>
        </div>
      </div>

      <main className="max-w-7xl mx-auto px-4 py-6">
        {loading ? (
          <div className="text-center py-20 text-gray-500">Loading RFPs...</div>
        ) : rfps.length === 0 ? (
          <div className="text-center py-20 text-gray-500">No RFPs found.</div>
        ) : (
          <>
            <div className="mb-4 text-sm text-gray-500">
              Showing {((page - 1) * PER_PAGE) + 1} to {Math.min(page * PER_PAGE, total)} of {total} results
            </div>
            <div className="space-y-3">
              {rfps.map(rfp => {
                const daysLeft = getDaysLeft(rfp.due_date)
                return (
                  <div key={rfp.id} className="bg-white rounded-lg border border-gray-200 p-4 hover:border-red-300 hover:shadow-sm transition-all">
                    <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-2">
                      <div className="flex-1">
                        <div className="flex items-center gap-2 mb-1 flex-wrap">
                          <span className="text-xs font-medium px-2 py-0.5 rounded" style={{ backgroundColor: '#FFF0F0', color: '#CC0000' }}>
                            {rfp.source_platform}
                          </span>
                          {rfp.agency && (
                            <span className="text-xs text-gray-500">{rfp.agency}</span>
                          )}
                          {rfp.includes_inclusion_plan && (
                            <span className="text-xs font-medium px-2 py-0.5 rounded bg-blue-50 text-blue-600">
                              Inclusion Plan
                            </span>
                          )}
                        </div>
                        <h2 className="font-semibold text-gray-900 text-sm md:text-base leading-snug">
                          {rfp.title}
                        </h2>
                        {rfp.description && (
                          <p className="text-gray-500 text-sm mt-1 line-clamp-2">
                            {rfp.description}
                          </p>
                        )}
                        <div className="flex items-center gap-4 mt-2 text-xs text-gray-400 flex-wrap">
                          {rfp.ref_number && (
                            <span>Ref: {rfp.ref_number}</span>
                          )}
                          {rfp.contact_name && (
                            <span>Contact: {rfp.contact_name}</span>
                          )}
                        </div>
                      </div>
                      <div className="flex flex-row md:flex-col items-center md:items-end gap-3 md:gap-1 md:min-w-32">
                        <div className="text-right">
                          <div className="text-xs text-gray-400">Due</div>
                          <div className="text-sm font-medium text-gray-700">
                            {formatDate(rfp.due_date)}
                          </div>
                          {daysLeft !== null && (
                            <div className={"text-xs " + getDaysColor(daysLeft)}>
                              {daysLeft < 0 ? 'Expired' : daysLeft === 0 ? 'Due today!' : daysLeft + ' days left'}
                            </div>
                          )}
                        </div>
                        {rfp.detail_url && (
                          
                            href={rfp.detail_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            style={{ backgroundColor: '#EE0000' }}
                            className="text-white text-xs font-medium px-3 py-1.5 rounded hover:opacity-90 transition-opacity whitespace-nowrap"
                          >
                            View RFP
                          </a>
                        )}
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
            {totalPages > 1 && (
              <div className="flex items-center justify-center gap-2 mt-8">
                <button
                  onClick={() => setPage(p => Math.max(1, p - 1))}
                  disabled={page === 1}
                  className="px-4 py-2 rounded border text-sm disabled:opacity-40 hover:bg-gray-100"
                >
                  Previous
                </button>
                <span className="text-sm text-gray-600">
                  Page {page} of {totalPages}
                </span>
                <button
                  onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                  disabled={page === totalPages}
                  className="px-4 py-2 rounded border text-sm disabled:opacity-40 hover:bg-gray-100"
                >
                  Next
                </button>
              </div>
            )}
          </>
        )}
      </main>

      <footer style={{ backgroundColor: '#151515' }} className="mt-12 py-6">
        <div className="max-w-7xl mx-auto px-4 text-center text-gray-500 text-sm">
          Washington State RFP Tracker — Updated daily from procurement sources across state agencies, counties, cities, and transit authorities.
        </div>
      </footer>
    </div>
  )
}
