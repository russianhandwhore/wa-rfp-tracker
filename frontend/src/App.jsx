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
  const [showExpired, setShowExpired] = useState(false)
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)

  useEffect(() => {
    fetchRfps()
  }, [search, platform, page, showExpired])

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
    if (!showExpired) {
      query = query.gte('due_date', new Date().toISOString())
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

  function scrollToSearch() {
    document.getElementById('search-section').scrollIntoView({ behavior: 'smooth' })
  }

  const totalPages = Math.ceil(total / PER_PAGE)

  return (
    <div className="min-h-screen bg-gray-50">

      <nav style={{ backgroundColor: '#151515' }} className="sticky top-0 z-50 border-b border-gray-800">
        <div className="max-w-7xl mx-auto px-4">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center gap-3">
              <div style={{ backgroundColor: '#EE0000' }} className="w-8 h-8 rounded flex items-center justify-center flex-shrink-0">
                <span className="text-white font-bold text-sm">WA</span>
              </div>
              <span className="text-white font-bold text-lg">WA RFP Tracker</span>
            </div>

            <div className="hidden md:flex items-center gap-8">
              <a href="#" className="text-gray-300 hover:text-white text-sm transition-colors">Home</a>
              <a href="#search-section" className="text-gray-300 hover:text-white text-sm transition-colors">Browse RFPs</a>
              <a href="#" className="text-gray-300 hover:text-white text-sm transition-colors">About</a>
              <a href="#" className="text-gray-300 hover:text-white text-sm transition-colors">Sources</a>
              <a href="#" className="text-gray-300 hover:text-white text-sm transition-colors">Contact</a>
              <a href="#search-section" onClick={scrollToSearch} style={{ backgroundColor: '#EE0000' }} className="text-white text-sm font-medium px-4 py-2 rounded hover:opacity-90 transition-opacity">
                Find RFPs
              </a>
            </div>

            <button
              className="md:hidden text-gray-300 hover:text-white"
              onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
            >
              <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={mobileMenuOpen ? "M6 18L18 6M6 6l12 12" : "M4 6h16M4 12h16M4 18h16"} />
              </svg>
            </button>
          </div>

          {mobileMenuOpen && (
            <div className="md:hidden pb-4 space-y-2">
              <a href="#" className="block text-gray-300 hover:text-white text-sm py-2">Home</a>
              <a href="#search-section" className="block text-gray-300 hover:text-white text-sm py-2">Browse RFPs</a>
              <a href="#" className="block text-gray-300 hover:text-white text-sm py-2">About</a>
              <a href="#" className="block text-gray-300 hover:text-white text-sm py-2">Sources</a>
              <a href="#" className="block text-gray-300 hover:text-white text-sm py-2">Contact</a>
            </div>
          )}
        </div>
      </nav>

      <section style={{ background: 'linear-gradient(135deg, #151515 0%, #1a1a2e 50%, #16213e 100%)' }} className="relative overflow-hidden">
        <div className="absolute inset-0 opacity-10">
          <div className="absolute top-20 left-10 w-72 h-72 rounded-full" style={{ backgroundColor: '#EE0000', filter: 'blur(80px)' }}></div>
          <div className="absolute bottom-10 right-20 w-96 h-96 rounded-full" style={{ backgroundColor: '#1a56db', filter: 'blur(100px)' }}></div>
        </div>

        <div className="relative max-w-7xl mx-auto px-4 py-24 md:py-32">
          <div className="max-w-3xl">
            <div className="flex items-center gap-2 mb-6">
              <span style={{ backgroundColor: '#EE000020', color: '#EE0000', border: '1px solid #EE000040' }} className="text-xs font-semibold px-3 py-1 rounded-full">
                Updated Daily
              </span>
              <span className="text-gray-400 text-xs">{total} active opportunities</span>
            </div>

            <h1 className="text-4xl md:text-6xl font-bold text-white leading-tight mb-6">
              Washington State
              <span style={{ color: '#EE0000' }}> RFP </span>
              Tracker
            </h1>

            <p className="text-gray-300 text-lg md:text-xl leading-relaxed mb-10 max-w-2xl">
              Find and track active procurement opportunities from every Washington State government agency, county, city, transit authority, port, and university — all in one place, updated every morning.
            </p>

            <div className="flex flex-col sm:flex-row gap-4">
              <button
                onClick={scrollToSearch}
                style={{ backgroundColor: '#EE0000' }}
                className="text-white font-semibold px-8 py-4 rounded-lg hover:opacity-90 transition-opacity text-lg"
              >
                Browse Active RFPs
              </button>
              
                href="#"
                className="text-white font-semibold px-8 py-4 rounded-lg border border-gray-600 hover:border-gray-400 transition-colors text-lg text-center"
              >
                Learn More
              </a>
            </div>

            <div className="flex items-center gap-8 mt-12 pt-12 border-t border-gray-800">
              <div>
                <div className="text-3xl font-bold text-white">{total}+</div>
                <div className="text-gray-400 text-sm">Active RFPs</div>
              </div>
              <div className="w-px h-10 bg-gray-700"></div>
              <div>
                <div className="text-3xl font-bold text-white">25+</div>
                <div className="text-gray-400 text-sm">Agency Sources</div>
              </div>
              <div className="w-px h-10 bg-gray-700"></div>
              <div>
                <div className="text-3xl font-bold text-white">Daily</div>
                <div className="text-gray-400 text-sm">Auto Updates</div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="bg-white border-b border-gray-200 py-8">
        <div className="max-w-7xl mx-auto px-4">
          <p className="text-center text-gray-400 text-sm mb-6">AGGREGATING PROCUREMENT DATA FROM</p>
          <div className="flex flex-wrap justify-center gap-6 md:gap-10">
            {['WEBS / DES', 'King County', 'City of Seattle', 'Sound Transit', 'Pierce County', 'Port of Seattle', 'UW', 'WSU', 'WSDOT', 'City of Tacoma'].map(agency => (
              <span key={agency} className="text-gray-500 text-sm font-medium">{agency}</span>
            ))}
          </div>
        </div>
      </section>

      <section id="search-section" className="py-8 sticky top-16 z-40 bg-white border-b border-gray-200 shadow-sm">
        <div className="max-w-7xl mx-auto px-4">
          <div className="flex flex-col md:flex-row gap-3">
            <div className="flex-1 relative">
              <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
              <input
                type="text"
                placeholder="Search RFPs by title or keyword..."
                value={search}
                onChange={e => { setSearch(e.target.value); setPage(1) }}
                className="w-full pl-10 pr-4 py-3 rounded-lg border border-gray-300 focus:outline-none focus:border-red-500 focus:ring-1 focus:ring-red-500 text-gray-900 placeholder-gray-400"
              />
            </div>
            <select
              value={platform}
              onChange={e => { setPlatform(e.target.value); setPage(1) }}
              className="px-4 py-3 rounded-lg border border-gray-300 focus:outline-none focus:border-red-500 text-gray-900 bg-white"
            >
              {PLATFORMS.map(p => (
                <option key={p} value={p}>{p === 'All' ? 'All Platforms' : p}</option>
              ))}
            </select>
            <label className="flex items-center gap-2 px-4 py-3 rounded-lg border border-gray-300 cursor-pointer hover:border-gray-400 bg-white">
              <input
                type="checkbox"
                checked={showExpired}
                onChange={e => { setShowExpired(e.target.checked); setPage(1) }}
                className="rounded"
              />
              <span className="text-sm text-gray-600 whitespace-nowrap">Show Expired</span>
            </label>
          </div>
        </div>
      </section>

      <main className="max-w-7xl mx-auto px-4 py-8">
        {loading ? (
          <div className="text-center py-20">
            <div className="inline-block w-8 h-8 border-4 border-red-500 border-t-transparent rounded-full animate-spin mb-4"></div>
            <p className="text-gray-500">Loading RFPs...</p>
          </div>
        ) : rfps.length === 0 ? (
          <div className="text-center py-20">
            <div className="text-6xl mb-4">🔍</div>
            <h3 className="text-xl font-semibold text-gray-700 mb-2">No RFPs found</h3>
            <p className="text-gray-500">Try adjusting your search or filters</p>
          </div>
        ) : (
          <>
            <div className="flex items-center justify-between mb-6">
              <p className="text-sm text-gray-500">
                Showing <span className="font-semibold text-gray-900">{((page - 1) * PER_PAGE) + 1}</span> to <span className="font-semibold text-gray-900">{Math.min(page * PER_PAGE, total)}</span> of <span className="font-semibold text-gray-900">{total}</span> results
              </p>
            </div>

            <div className="space-y-4">
              {rfps.map(rfp => {
                const daysLeft = getDaysLeft(rfp.due_date)
                return (
                  <div key={rfp.id} className="bg-white rounded-xl border border-gray-200 p-5 hover:border-red-300 hover:shadow-md transition-all group">
                    <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-4">
                      <div className="flex-1">
                        <div className="flex items-center gap-2 mb-2 flex-wrap">
                          <span className="text-xs font-semibold px-2.5 py-1 rounded-full" style={{ backgroundColor: '#FFF0F0', color: '#CC0000' }}>
                            {rfp.source_platform}
                          </span>
                          {rfp.agency && (
                            <span className="text-xs font-medium px-2.5 py-1 rounded-full bg-gray-100 text-gray-600">
                              {rfp.agency}
                            </span>
                          )}
                          {rfp.includes_inclusion_plan && (
                            <span className="text-xs font-medium px-2.5 py-1 rounded-full bg-blue-50 text-blue-600">
                              Inclusion Plan
                            </span>
                          )}
                        </div>

                        <h2 className="font-bold text-gray-900 text-base md:text-lg leading-snug mb-2 group-hover:text-red-700 transition-colors">
                          {rfp.title}
                        </h2>

                        {rfp.description && (
                          <p className="text-gray-500 text-sm leading-relaxed mb-3 line-clamp-2">
                            {rfp.description}
                          </p>
                        )}

                        <div className="flex items-center gap-4 text-xs text-gray-400 flex-wrap">
                          {rfp.ref_number && (
                            <span className="flex items-center gap-1">
                              <span className="font-medium text-gray-500">Ref:</span> {rfp.ref_number}
                            </span>
                          )}
                          {rfp.contact_name && (
                            <span className="flex items-center gap-1">
                              <span className="font-medium text-gray-500">Contact:</span> {rfp.contact_name}
                            </span>
                          )}
                        </div>
                      </div>

                      <div className="flex flex-row md:flex-col items-center md:items-end gap-4 md:gap-2 md:min-w-36 flex-shrink-0">
                        <div className="text-right">
                          <div className="text-xs text-gray-400 uppercase tracking-wide mb-1">Due Date</div>
                          <div className="text-sm font-bold text-gray-800">{formatDate(rfp.due_date)}</div>
                          {daysLeft !== null && (
                            <div className={"text-xs mt-0.5 " + getDaysColor(daysLeft)}>
                              {daysLeft < 0 ? 'Expired' : daysLeft === 0 ? 'Due today!' : daysLeft + ' days left'}
                            </div>
                          )}
                        </div>
                        {rfp.detail_url && (
                          
                            href={rfp.detail_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            style={{ backgroundColor: '#EE0000' }}
                            className="text-white text-xs font-semibold px-4 py-2 rounded-lg hover:opacity-90 transition-opacity whitespace-nowrap"
                          >
                            View RFP →
                          </a>
                        )}
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>

            {totalPages > 1 && (
              <div className="flex items-center justify-center gap-2 mt-10">
                <button
                  onClick={() => setPage(p => Math.max(1, p - 1))}
                  disabled={page === 1}
                  className="px-5 py-2.5 rounded-lg border border-gray-300 text-sm font-medium disabled:opacity-40 hover:bg-gray-50 transition-colors"
                >
                  ← Previous
                </button>
                <div className="flex items-center gap-1">
                  {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
                    const pageNum = Math.max(1, Math.min(page - 2, totalPages - 4)) + i
                    return (
                      <button
                        key={pageNum}
                        onClick={() => setPage(pageNum)}
                        className={"px-4 py-2.5 rounded-lg text-sm font-medium transition-colors " + (page === pageNum ? "text-white" : "border border-gray-300 hover:bg-gray-50")}
                        style={page === pageNum ? { backgroundColor: '#EE0000' } : {}}
                      >
                        {pageNum}
                      </button>
                    )
                  })}
                </div>
                <button
                  onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                  disabled={page === totalPages}
                  className="px-5 py-2.5 rounded-lg border border-gray-300 text-sm font-medium disabled:opacity-40 hover:bg-gray-50 transition-colors"
                >
                  Next →
                </button>
              </div>
            )}
          </>
        )}
      </main>

      <footer style={{ backgroundColor: '#151515' }} className="mt-16">
        <div className="max-w-7xl mx-auto px-4 py-12">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-8 mb-10">
            <div className="md:col-span-2">
              <div className="flex items-center gap-2 mb-4">
                <div style={{ backgroundColor: '#EE0000' }} className="w-7 h-7 rounded flex items-center justify-center">
                  <span className="text-white font-bold text-xs">WA</span>
                </div>
                <span className="text-white font-bold">WA RFP Tracker</span>
              </div>
              <p className="text-gray-400 text-sm leading-relaxed max-w-sm">
                The most comprehensive source for Washington State government procurement opportunities. Updated daily from 25+ official sources.
              </p>
            </div>
            <div>
              <h4 className="text-white font-semibold mb-4 text-sm uppercase tracking-wide">Navigation</h4>
              <div className="space-y-2">
                <a href="#" className="block text-gray-400 hover:text-white text-sm transition-colors">Home</a>
                <a href="#search-section" className="block text-gray-400 hover:text-white text-sm transition-colors">Browse RFPs</a>
                <a href="#" className="block text-gray-400 hover:text-white text-sm transition-colors">About</a>
                <a href="#" className="block text-gray-400 hover:text-white text-sm transition-colors">Contact</a>
              </div>
            </div>
            <div>
              <h4 className="text-white font-semibold mb-4 text-sm uppercase tracking-wide">Sources</h4>
              <div className="space-y-2">
                <a href="https://des.wa.gov/sell/bid-opportunities" target="_blank" rel="noopener noreferrer" className="block text-gray-400 hover:text-white text-sm transition-colors">WEBS / DES</a>
                <a href="https://procurement.opengov.com/portal/seattle" target="_blank" rel="noopener noreferrer" className="block text-gray-400 hover:text-white text-sm transition-colors">City of Seattle</a>
                <a href="https://procurement.opengov.com/portal/piercecountywa" target="_blank" rel="noopener noreferrer" className="block text-gray-400 hover:text-white text-sm transition-colors">Pierce County</a>
                <a href="https://soundtransit.biddingo.com" target="_blank" rel="noopener noreferrer" className="block text-gray-400 hover:text-white text-sm transition-colors">Sound Transit</a>
              </div>
            </div>
          </div>
          <div className="border-t border-gray-800 pt-6 flex flex-col md:flex-row items-center justify-between gap-4">
            <p className="text-gray-500 text-sm">
              Washington State RFP Tracker — Not affiliated with any government agency. Data sourced from public procurement portals.
            </p>
            <p className="text-gray-600 text-xs">Updated daily at 7am Pacific</p>
          </div>
        </div>
      </footer>
    </div>
  )
}
