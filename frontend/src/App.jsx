import { useState, useEffect } from 'react'
import { createClient } from '@supabase/supabase-js'

const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL,
  import.meta.env.VITE_SUPABASE_ANON_KEY
)

const PLATFORMS = ['All', 'WEBS', 'OpenGov', 'Port of Seattle', 'Procureware', 'Sound Transit', 'PublicPurchase', 'SAP_Ariba', 'Oracle', 'Bonfire', 'Workday', 'Biddingo', 'Standalone']
const CATEGORIES = ['All', 'IT', 'Construction', 'Supplies', 'Services', 'Misc']
const SORT_OPTIONS = [
  { label: 'Newest First', value: 'created_at_desc' },
  { label: 'Oldest First', value: 'created_at_asc' },
  { label: 'Due Soonest', value: 'due_date_asc' },
  { label: 'Due Latest', value: 'due_date_desc' },
]
const PER_PAGE = 25

// Ref number pattern — Procureware titles that are just a ref number with no real name
const REF_ONLY_PATTERN = /^(RFP|RFQ|ITB|IFB|RFI|IRFP|PW|BID|SOQ|EOI|QBS)[\s\-][\w\-\.]+$/i

function isBlankCard(rfp) {
  // Skip Procureware cards that only have a ref number as the title and no description
  if (rfp.source_platform === 'Procureware') {
    const titleIsRefOnly = REF_ONLY_PATTERN.test((rfp.title || '').trim())
    if (titleIsRefOnly && !rfp.description) return true
  }
  // Skip any card with no title at all
  if (!rfp.title || rfp.title.trim().length < 3) return true
  return false
}

export default function App() {
  const [rfps, setRfps] = useState([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [platform, setPlatform] = useState('All')
  const [category, setCategory] = useState('All')
  const [sortBy, setSortBy] = useState('created_at_desc')
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [nearTotal, setNearTotal] = useState(0)
  const [showEvaluating, setShowEvaluating] = useState(false)
  const [showFuture, setShowFuture] = useState(false)
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const [docsModal, setDocsModal] = useState(null)
  const [contactModal, setContactModal] = useState(null)
  const [contactInfo, setContactInfo] = useState(null)
  const [contactLoading, setContactLoading] = useState(false)

  useEffect(() => { fetchRfps() }, [search, platform, page, showEvaluating, showFuture, category, sortBy])

  useEffect(() => {
    const handler = (e) => {
      if (e.key === 'Escape') { setDocsModal(null); setContactModal(null) }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  async function fetchRfps() {
    setLoading(true)

    const now = new Date().toISOString()
    const sortMap = {
      'created_at_desc':  { col: 'created_at', asc: false, nullsFirst: false },
      'created_at_asc':   { col: 'created_at', asc: true,  nullsFirst: false },
      'due_date_asc':     { col: 'due_date',   asc: true,  nullsFirst: false },
      'due_date_desc':    { col: 'due_date',   asc: false, nullsFirst: false },
    }
    const sort = sortMap[sortBy] || sortMap['created_at_desc']

    let query = supabase
      .from('rfps')
      .select('*', { count: 'exact' })
      .in('status', showFuture ? ['upcoming'] : ['active'])
      .or(`due_date.gte.${now},due_date.is.null`)
      .order(sort.col, { ascending: sort.asc, nullsFirst: sort.nullsFirst })
      .range((page - 1) * PER_PAGE, page * PER_PAGE - 1)

    if (search) query = query.ilike('title', '%' + search + '%')
    if (platform !== 'All') query = query.eq('source_platform', platform)
    if (category !== 'All') query = query.contains('categories', [category])
    const { data, count, error } = await query
    if (!error) {
      const filtered = (data || []).filter(r => {
        if (isBlankCard(r)) return false

        if (!showEvaluating) {
          try {
            const raw = JSON.parse(r.raw_data || '{}')
            if (raw.phase_label === 'Evaluating') return false
          } catch { return true }
        }
        return true
      })
      setRfps(filtered)
      setTotal(count || 0)
    }
    setLoading(false)
  }

  function formatDate(dateStr) {
    if (!dateStr) return 'TBD'
    return new Date(dateStr).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
  }

  function getDaysLeft(dateStr) {
    if (!dateStr) return null
    return Math.ceil((new Date(dateStr) - new Date()) / (1000 * 60 * 60 * 24))
  }

  function getDaysColor(days) {
    if (days === null || days < 0) return 'text-gray-400'
    if (days <= 3) return 'text-red-500 font-bold'
    if (days <= 7) return 'text-orange-500 font-semibold'
    if (days <= 30) return 'text-green-600 font-medium'
    if (days <= 100) return 'text-green-600'
    return 'text-gray-400'
  }

  function getCategoryColor(cat) {
    const colors = {
      IT: 'bg-blue-50 text-blue-700 border-blue-200',
      Construction: 'bg-orange-50 text-orange-700 border-orange-200',
      Supplies: 'bg-green-50 text-green-700 border-green-200',
      Services: 'bg-purple-50 text-purple-700 border-purple-200',
      Misc: 'bg-gray-50 text-gray-700 border-gray-200',
    }
    return colors[cat] || 'bg-gray-50 text-gray-700 border-gray-200'
  }

  function getDocuments(rfp) {
    if (!rfp.raw_data) return []
    try {
      const parsed = typeof rfp.raw_data === 'string' ? JSON.parse(rfp.raw_data) : rfp.raw_data
      return parsed.documents || []
    } catch { return [] }
  }

  function getBidDocsUrl(rfp) {
    if (!rfp.raw_data) return null
    try {
      const parsed = typeof rfp.raw_data === 'string' ? JSON.parse(rfp.raw_data) : rfp.raw_data
      return parsed.bid_documents_url || null
    } catch { return null }
  }

  function openDocsModal(rfp) {
    setDocsModal({ title: rfp.title, detailUrl: rfp.detail_url, documents: getDocuments(rfp) })
  }

  async function openContactModal(rfp) {
    const name = rfp.contact_name || null
    const agency = rfp.agency || ''
    const dept = rfp.department || ''
    const displayOrg = agency || dept || 'Washington State Government'

    // searchName: best available term for AI lookup and Google search
    const searchName = name || rfp.contact_email || agency
    const googleQuery = [searchName, 'Washington State procurement'].filter(Boolean).join(' ')
    const linkedinQuery = [name, agency || dept, 'Washington'].filter(Boolean).join(' ')

    setContactModal({
      name: name || rfp.contact_email || 'Contact',
      agency: displayOrg,
      department: dept || null,
      email: rfp.contact_email || null,
      googleUrl: 'https://www.google.com/search?q=' + encodeURIComponent(googleQuery),
      linkedinUrl: 'https://www.linkedin.com/search/results/people/?keywords=' + encodeURIComponent(linkedinQuery),
    })
    setContactInfo(null)

    // Always run AI lookup — use name, email, or agency as search term
    if (searchName) {
      setContactLoading(true)
      try {
        const res = await fetch('/api/contact', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: searchName, agency, department: dept })
        })
        if (!res.ok) throw new Error('Failed')
        const parsed = await res.json()
        setContactInfo(parsed)
      } catch (e) {
        setContactInfo({ _error: true })
      }
      setContactLoading(false)
    }
  }

  function scrollToSearch() {
    document.getElementById('search-section').scrollIntoView({ behavior: 'smooth' })
  }

  const totalPages = Math.ceil(total / PER_PAGE)

  return (
    <div className="min-h-screen bg-gray-50">

      {/* Documents Modal */}
      {docsModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ backgroundColor: 'rgba(0,0,0,0.6)' }} onClick={() => setDocsModal(null)}>
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg max-h-screen overflow-hidden flex flex-col" onClick={e => e.stopPropagation()}>
            <div className="flex items-start justify-between p-6 border-b border-gray-100">
              <div className="flex-1 pr-4">
                <span className="text-xs font-semibold px-2 py-0.5 rounded-full mb-2 inline-block" style={{ backgroundColor: '#FFF0F0', color: '#CC0000' }}>Procureware</span>
                <h2 className="font-bold text-gray-900 text-base leading-snug">{docsModal.title}</h2>
              </div>
              <button onClick={() => setDocsModal(null)} className="text-gray-400 hover:text-gray-600 transition-colors flex-shrink-0">
                <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
              </button>
            </div>
            <div className="overflow-y-auto flex-1 p-6">
              {docsModal.documents.length === 0 ? (
                <div className="text-center py-8">
                  <div className="text-4xl mb-3">📂</div>
                  <p className="text-gray-500 text-sm mb-4">No documents were scraped for this bid.</p>
                  {docsModal.detailUrl && (
                    <a href={docsModal.detailUrl + '?t=BidDocuments'} target="_blank" rel="noopener noreferrer" style={{ backgroundColor: '#EE0000' }} className="inline-block text-white text-sm font-semibold px-4 py-2 rounded-lg hover:opacity-90">
                      View Documents on Procureware →
                    </a>
                  )}
                </div>
              ) : (
                <div className="space-y-2">
                  <p className="text-xs text-gray-400 uppercase tracking-wide font-semibold mb-3">{docsModal.documents.length} document{docsModal.documents.length !== 1 ? 's' : ''} available</p>
                  {docsModal.documents.map((doc, i) => (
                    <a key={i} href={doc.url} target="_blank" rel="noopener noreferrer" className="flex items-center gap-3 p-3 rounded-lg border border-gray-200 hover:border-red-300 hover:bg-red-50 transition-all group">
                      <div className="w-8 h-8 rounded-lg bg-gray-100 flex items-center justify-center flex-shrink-0 group-hover:bg-red-100">
                        <svg className="w-4 h-4 text-gray-500 group-hover:text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
                      </div>
                      <span className="text-sm text-gray-700 group-hover:text-red-700 font-medium flex-1 truncate">{doc.name}</span>
                      <svg className="w-4 h-4 text-gray-400 group-hover:text-red-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" /></svg>
                    </a>
                  ))}
                </div>
              )}
            </div>
            {docsModal.detailUrl && (
              <div className="p-4 border-t border-gray-100 bg-gray-50">
                <a href={docsModal.detailUrl} target="_blank" rel="noopener noreferrer" className="text-sm text-gray-500 hover:text-red-600 transition-colors">View full bid on Procureware →</a>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Contact Modal */}
      {contactModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ backgroundColor: 'rgba(0,0,0,0.6)' }} onClick={() => setContactModal(null)}>
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-sm overflow-hidden" onClick={e => e.stopPropagation()}>
            <div className="flex items-start justify-between p-6 border-b border-gray-100">
              <div>
                <p className="text-xs text-gray-400 uppercase tracking-wide font-semibold mb-1">Procurement Contact</p>
                <h2 className="font-bold text-gray-900 text-xl">{contactModal.name}</h2>
                {contactModal.agency && <p className="text-sm text-gray-500 mt-0.5">{contactModal.agency}</p>}
                {contactModal.department && <p className="text-xs text-gray-400 mt-0.5">{contactModal.department}</p>}
              </div>
              <button onClick={() => setContactModal(null)} className="text-gray-400 hover:text-gray-600 ml-4 flex-shrink-0">
                <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
              </button>
            </div>
            <div className="p-6 space-y-4">
              {contactLoading ? (
                <div className="flex items-center gap-3 p-4 bg-gray-50 rounded-xl">
                  <div className="w-5 h-5 border-2 border-red-500 border-t-transparent rounded-full animate-spin flex-shrink-0"></div>
                  <p className="text-sm text-gray-500">Searching for contact info...</p>
                </div>
              ) : contactInfo && !contactInfo._error ? (
                <div className="space-y-2">
                  {contactInfo.name && contactInfo.name !== 'null' && (
                    <div className="flex items-center gap-3 p-3 bg-gray-50 rounded-xl">
                      <span className="text-lg">👤</span>
                      <div><p className="text-xs text-gray-400">Name</p><p className="text-sm font-medium text-gray-800">{contactInfo.name}</p></div>
                    </div>
                  )}
                  {contactInfo.title && contactInfo.title !== 'null' && (
                    <div className="flex items-center gap-3 p-3 bg-gray-50 rounded-xl">
                      <span className="text-lg">🏢</span>
                      <div><p className="text-xs text-gray-400">Title</p><p className="text-sm font-medium text-gray-800">{contactInfo.title}</p></div>
                    </div>
                  )}
                  {(contactInfo.email && contactInfo.email !== 'null' ? contactInfo.email : contactModal.email) && (
                    <a href={'mailto:' + (contactInfo.email && contactInfo.email !== 'null' ? contactInfo.email : contactModal.email)} className="flex items-center gap-3 p-3 bg-gray-50 hover:bg-red-50 rounded-xl transition-colors group">
                      <span className="text-lg">📧</span>
                      <div><p className="text-xs text-gray-400">Email</p><p className="text-sm font-medium text-red-600 group-hover:underline">{contactInfo.email && contactInfo.email !== 'null' ? contactInfo.email : contactModal.email}</p></div>
                    </a>
                  )}
                  {contactInfo.phone && contactInfo.phone !== 'null' && (
                    <a href={'tel:' + contactInfo.phone} className="flex items-center gap-3 p-3 bg-gray-50 hover:bg-red-50 rounded-xl transition-colors group">
                      <span className="text-lg">📞</span>
                      <div><p className="text-xs text-gray-400">Phone</p><p className="text-sm font-medium text-red-600 group-hover:underline">{contactInfo.phone}</p></div>
                    </a>
                  )}
                  {!contactInfo.email && !contactInfo.phone && !contactInfo.title && !contactModal.email && (
                    <p className="text-xs text-gray-400 text-center py-2 bg-gray-50 rounded-xl p-3">No details found automatically — try searching below</p>
                  )}
                </div>
              ) : contactInfo?._error ? (
                <p className="text-xs text-gray-400 text-center py-2 bg-gray-50 rounded-xl p-3">Could not load contact details — use the search buttons below</p>
              ) : contactModal.email ? (
                <a href={'mailto:' + contactModal.email} className="flex items-center gap-3 p-3 bg-gray-50 hover:bg-red-50 rounded-xl transition-colors group">
                  <span className="text-lg">📧</span>
                  <div><p className="text-xs text-gray-400">Email</p><p className="text-sm font-medium text-red-600 group-hover:underline">{contactModal.email}</p></div>
                </a>
              ) : null}

              <div>
                <p className="text-xs text-gray-400 mb-2 text-center">Search manually</p>
                <div className="grid grid-cols-2 gap-3">
                  <a href={contactModal.googleUrl} target="_blank" rel="noopener noreferrer"
                    className="flex items-center justify-center gap-2 py-3 px-4 rounded-xl border-2 border-gray-200 hover:border-red-400 hover:bg-red-50 transition-all text-sm font-semibold text-gray-700 hover:text-red-700">
                    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="currentColor"><path d="M12 11h8.533c.044.385.067.78.067 1.184C20.6 17.48 17.04 21 12 21c-4.97 0-9-4.03-9-9s4.03-9 9-9c2.395 0 4.565.94 6.185 2.47L16.2 7.4A6.5 6.5 0 1018.5 12H12v-1z" /></svg>
                    Google
                  </a>
                  <a href={contactModal.linkedinUrl} target="_blank" rel="noopener noreferrer"
                    className="flex items-center justify-center gap-2 py-3 px-4 rounded-xl border-2 border-gray-200 hover:border-blue-400 hover:bg-blue-50 transition-all text-sm font-semibold text-gray-700 hover:text-blue-700">
                    <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24"><path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/></svg>
                    LinkedIn
                  </a>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Navbar */}
      <nav style={{ backgroundColor: '#151515' }} className="sticky top-0 z-40 border-b border-gray-800">
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
              <button onClick={scrollToSearch} style={{ backgroundColor: '#EE0000' }} className="text-white text-sm font-medium px-4 py-2 rounded hover:opacity-90 transition-opacity">Find RFPs</button>
            </div>
            <button className="md:hidden text-gray-300 hover:text-white" onClick={() => setMobileMenuOpen(!mobileMenuOpen)}>
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
              <a href="#" className="block text-gray-300 hover:text-white text-sm py-2">Contact</a>
            </div>
          )}
        </div>
      </nav>

      {/* Hero */}
      <section style={{ background: 'linear-gradient(135deg, #151515 0%, #1a1a2e 50%, #16213e 100%)' }} className="relative overflow-hidden">
        <div className="absolute inset-0 opacity-10">
          <div className="absolute top-20 left-10 w-72 h-72 rounded-full" style={{ backgroundColor: '#EE0000', filter: 'blur(80px)' }}></div>
          <div className="absolute bottom-10 right-20 w-96 h-96 rounded-full" style={{ backgroundColor: '#1a56db', filter: 'blur(100px)' }}></div>
        </div>
        <div className="relative max-w-7xl mx-auto px-4 py-24 md:py-32">
          <div className="max-w-3xl">
            <div className="flex items-center gap-2 mb-6">
              <span style={{ backgroundColor: '#EE000020', color: '#EE0000', border: '1px solid #EE000040' }} className="text-xs font-semibold px-3 py-1 rounded-full">Updated Daily</span>
              <span className="text-gray-400 text-xs">{total} active opportunities</span>
            </div>
            <h1 className="text-4xl md:text-6xl font-bold text-white leading-tight mb-6">
              Washington State<span style={{ color: '#EE0000' }}> RFP </span>Tracker
            </h1>
            <p className="text-gray-300 text-lg md:text-xl leading-relaxed mb-10 max-w-2xl">
              Find and track active procurement opportunities from every Washington State government agency, county, city, transit authority, port, and university — all in one place, updated every morning.
            </p>
            <div className="flex flex-col sm:flex-row gap-4">
              <button onClick={scrollToSearch} style={{ backgroundColor: '#EE0000' }} className="text-white font-semibold px-8 py-4 rounded-lg hover:opacity-90 transition-opacity text-lg">Browse Active RFPs</button>
              <a href="#" className="text-white font-semibold px-8 py-4 rounded-lg border border-gray-600 hover:border-gray-400 transition-colors text-lg text-center">Learn More</a>
            </div>
            <div className="flex items-center gap-8 mt-12 pt-12 border-t border-gray-800">
              <div><div className="text-3xl font-bold text-white">{total}+</div><div className="text-gray-400 text-sm">Active RFPs</div></div>
              <div className="w-px h-10 bg-gray-700"></div>
              <div><div className="text-3xl font-bold text-white">25+</div><div className="text-gray-400 text-sm">Agency Sources</div></div>
              <div className="w-px h-10 bg-gray-700"></div>
              <div><div className="text-3xl font-bold text-white">Daily</div><div className="text-gray-400 text-sm">Auto Updates</div></div>
            </div>
          </div>
        </div>
      </section>

      {/* Agency bar */}
      <section className="bg-white border-b border-gray-200 py-8">
        <div className="max-w-7xl mx-auto px-4">
          <p className="text-center text-gray-400 text-sm mb-6">AGGREGATING PROCUREMENT DATA FROM</p>
          <div className="flex flex-wrap justify-center gap-6 md:gap-10">
            {['WEBS / DES', 'King County', 'City of Seattle', 'Sound Transit', 'Pierce County', 'Port of Seattle', 'UW', 'WSU', 'WSDOT', 'City of Tacoma'].map(a => (
              <span key={a} className="text-gray-500 text-sm font-medium">{a}</span>
            ))}
          </div>
        </div>
      </section>

      {/* Search & Filters */}
      <section id="search-section" className="py-6 sticky top-16 z-30 bg-white border-b border-gray-200 shadow-sm">
        <div className="max-w-7xl mx-auto px-4">
          <div className="flex flex-col md:flex-row gap-3 mb-4">
            <div className="flex-1 relative">
              <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" /></svg>
              <input
                type="text"
                placeholder="Search RFPs by title or keyword..."
                value={search}
                onChange={e => { setSearch(e.target.value); setPage(1) }}
                className="w-full pl-10 pr-4 py-3 rounded-lg border border-gray-300 focus:outline-none focus:border-red-500 focus:ring-1 focus:ring-red-500 text-gray-900 placeholder-gray-400"
              />
            </div>
            <select value={platform} onChange={e => { setPlatform(e.target.value); setPage(1) }} className="px-4 py-3 rounded-lg border border-gray-300 focus:outline-none focus:border-red-500 text-gray-900 bg-white">
              {PLATFORMS.map(p => <option key={p} value={p}>{p === 'All' ? 'All Platforms' : p}</option>)}
            </select>
            <select value={sortBy} onChange={e => { setSortBy(e.target.value); setPage(1) }} className="px-4 py-3 rounded-lg border border-gray-300 focus:outline-none focus:border-red-500 text-gray-900 bg-white">
              {SORT_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
            <label className="flex items-center gap-2 px-4 py-3 rounded-lg border border-gray-300 cursor-pointer hover:border-gray-400 bg-white">
              <input type="checkbox" checked={showEvaluating} onChange={e => { setShowEvaluating(e.target.checked); setPage(1) }} className="rounded" />
              <span className="text-sm text-gray-600 whitespace-nowrap">RFPs Under Eval</span>
            </label>
            <button
              onClick={() => { setShowFuture(f => !f); setPage(1) }}
              className={"px-4 py-3 rounded-lg border-2 text-sm font-semibold transition-all whitespace-nowrap " + (showFuture ? "text-white border-transparent" : "bg-white border-gray-300 text-gray-600 hover:border-gray-400")}
              style={showFuture ? { backgroundColor: '#EE0000', borderColor: '#EE0000' } : {}}
            >
              🔮 Future Projects
            </button>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide mr-1">Category:</span>
            {CATEGORIES.map(cat => (
              <button
                key={cat}
                onClick={() => { setCategory(cat); setPage(1) }}
                className={"px-3 py-1.5 rounded-full text-xs font-semibold border transition-all " + (category === cat ? "text-white border-transparent" : "bg-white hover:bg-gray-50 " + getCategoryColor(cat))}
                style={category === cat ? { backgroundColor: '#EE0000', borderColor: '#EE0000' } : {}}
              >
                {cat === 'All' ? '🔍 All' : cat === 'IT' ? '💻 IT' : cat === 'Construction' ? '🏗️ Construction' : cat === 'Supplies' ? '📦 Supplies' : cat === 'Services' ? '🤝 Services' : '📋 Misc'}
              </button>
            ))}
          </div>
        </div>
      </section>

      {/* RFP List */}
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
                Showing <span className="font-semibold text-gray-900">{((page - 1) * PER_PAGE) + 1}</span>–<span className="font-semibold text-gray-900">{Math.min(page * PER_PAGE, total)}</span> of <span className="font-semibold text-gray-900">{total}</span> results
                {category !== 'All' && <span className="ml-2 text-red-600 font-medium">in {category}</span>}
              </p>
            </div>

            <div className="space-y-4">
              {rfps.map(rfp => {
                const daysLeft = getDaysLeft(rfp.due_date)
                const docs = getDocuments(rfp)
                const hasDocuments = rfp.source_platform === 'Procureware'
                const isOpenGov = rfp.source_platform === 'OpenGov'
                return (
                  <div key={rfp.id} className="bg-white rounded-xl border border-gray-200 p-5 hover:shadow-md hover:border-red-300 transition-all group">
                    <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-4">
                      <div className="flex-1">
                        <div className="flex items-center gap-2 mb-2 flex-wrap">
                          <span className="text-xs font-semibold px-2.5 py-1 rounded-full" style={{ backgroundColor: '#FFF0F0', color: '#CC0000' }}>{rfp.source_platform}</span>
                          {rfp.agency && rfp.agency !== rfp.source_platform && <span className="text-xs font-medium px-2.5 py-1 rounded-full bg-gray-100 text-gray-600">{rfp.agency}</span>}
                          {rfp.status === 'upcoming' && rfp.source_platform === 'Port of Seattle' && (
                            <span className="text-xs font-semibold px-2.5 py-1 rounded-full bg-blue-50 text-blue-700 border border-blue-200">🔮 Upcoming</span>
                          )}
                          {rfp.source_platform === 'Sound Transit' && (() => {
                            const phase = (() => { try { return JSON.parse(rfp.raw_data || '{}').phase_label } catch { return null } })()
                            if (!phase) return null
                            if (phase === 'Upcoming') return <span className="text-xs font-semibold px-2.5 py-1 rounded-full bg-blue-50 text-blue-700 border border-blue-200">🔮 Upcoming</span>
                            if (phase === 'Advertising') return <span className="text-xs font-semibold px-2.5 py-1 rounded-full bg-green-50 text-green-700 border border-green-200">📢 Advertising</span>
                            if (phase === 'Evaluating') return <span className="text-xs font-semibold px-2.5 py-1 rounded-full bg-yellow-50 text-yellow-700 border border-yellow-200">⏳ Evaluating</span>
                            return null
                          })()}
                          {rfp.categories && rfp.categories.map(cat => (
                            <span key={cat} className={"text-xs font-medium px-2.5 py-1 rounded-full border " + getCategoryColor(cat)}>
                              {cat === 'IT' ? '💻 IT' : cat === 'Construction' ? '🏗️ Construction' : cat === 'Supplies' ? '📦 Supplies' : cat === 'Services' ? '🤝 Services' : '📋 Misc'}
                            </span>
                          ))}
                          {rfp.includes_inclusion_plan && <span className="text-xs font-medium px-2.5 py-1 rounded-full bg-blue-50 text-blue-600 border border-blue-200">Inclusion Plan</span>}
                          {hasDocuments && docs.length > 0 && <span className="text-xs font-medium px-2.5 py-1 rounded-full bg-green-50 text-green-700 border border-green-200">📄 {docs.length} doc{docs.length !== 1 ? 's' : ''}</span>}
                        </div>

                        <h2 className="font-bold text-gray-900 text-base md:text-lg leading-snug mb-2 group-hover:text-red-700 transition-colors">{rfp.title}</h2>

                        {rfp.description && <p className="text-gray-500 text-sm leading-relaxed mb-3 line-clamp-2">{rfp.description}</p>}

                        <div className="flex items-center gap-4 text-xs text-gray-400 flex-wrap">
                          {rfp.ref_number && <span><span className="font-medium text-gray-500">Ref:</span> {rfp.ref_number}</span>}
                          {(rfp.contact_name || rfp.contact_email) && (
                            <button onClick={() => openContactModal(rfp)} className="flex items-center gap-1 hover:text-red-600 transition-colors group/c">
                              <span className="font-medium text-gray-500 group-hover/c:text-red-500">Contact:</span>
                              <span className="underline decoration-dotted underline-offset-2">{rfp.contact_name || rfp.contact_email}</span>
                              <svg className="w-3 h-3 text-gray-300 group-hover/c:text-red-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
                            </button>
                          )}
                        </div>
                      </div>

                      <div className="flex flex-row md:flex-col items-center md:items-end gap-3 md:gap-2 md:min-w-36 flex-shrink-0">
                        <div className="text-right">
                          <div className="text-xs text-gray-400 uppercase tracking-wide mb-1">Due Date</div>
                          <div className={"text-sm font-bold text-gray-800"}>{formatDate(rfp.due_date)}</div>
                          {daysLeft !== null && (
                            <div className="text-xs mt-0.5 text-gray-900">
                              {daysLeft < 0 ? 'Expired' : daysLeft === 0 ? 'Due today!' : daysLeft + ' days left'}
                            </div>
                          )}
                        </div>
                        <div className="flex flex-col gap-2 w-full md:items-end">
                          {rfp.detail_url && (
                            <a href={rfp.detail_url} target="_blank" rel="noopener noreferrer" style={{ backgroundColor: '#EE0000' }} className="text-white text-xs font-semibold px-4 py-2 rounded-lg hover:opacity-90 transition-opacity whitespace-nowrap text-center">
                              View RFP →
                            </a>
                          )}
                          {isOpenGov && rfp.detail_url && (
                            <a href={rfp.detail_url + '/downloads'} target="_blank" rel="noopener noreferrer" className="text-xs font-semibold px-4 py-2 rounded-lg border-2 border-gray-300 hover:border-red-400 hover:text-red-600 transition-all whitespace-nowrap bg-white text-center">
                              📄 Documents
                            </a>
                          )}
                          {hasDocuments && (() => {
                            const bidDocsUrl = getBidDocsUrl(rfp)
                            return bidDocsUrl
                              ? <a href={bidDocsUrl} target="_blank" rel="noopener noreferrer" className="text-xs font-semibold px-4 py-2 rounded-lg border-2 border-gray-300 hover:border-red-400 hover:text-red-600 transition-all whitespace-nowrap bg-white">📄 Bid Docs</a>
                              : <button onClick={() => openDocsModal(rfp)} className="text-xs font-semibold px-4 py-2 rounded-lg border-2 border-gray-300 hover:border-red-400 hover:text-red-600 transition-all whitespace-nowrap bg-white">📄 Documents</button>
                          })()}
                        </div>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>

            {totalPages > 1 && (
              <div className="flex items-center justify-center gap-2 mt-10">
                <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1} className="px-5 py-2.5 rounded-lg border border-gray-300 text-sm font-medium disabled:opacity-40 hover:bg-gray-50 transition-colors">← Previous</button>
                <div className="flex items-center gap-1">
                  {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
                    const pageNum = Math.max(1, Math.min(page - 2, totalPages - 4)) + i
                    return (
                      <button key={pageNum} onClick={() => setPage(pageNum)} className={"px-4 py-2.5 rounded-lg text-sm font-medium transition-colors " + (page === pageNum ? "text-white" : "border border-gray-300 hover:bg-gray-50")} style={page === pageNum ? { backgroundColor: '#EE0000' } : {}}>
                        {pageNum}
                      </button>
                    )
                  })}
                </div>
                <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages} className="px-5 py-2.5 rounded-lg border border-gray-300 text-sm font-medium disabled:opacity-40 hover:bg-gray-50 transition-colors">Next →</button>
              </div>
            )}
          </>
        )}
      </main>

      {/* Footer */}
      <footer style={{ backgroundColor: '#151515' }} className="mt-16">
        <div className="max-w-7xl mx-auto px-4 py-12">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-8 mb-10">
            <div className="md:col-span-2">
              <div className="flex items-center gap-2 mb-4">
                <div style={{ backgroundColor: '#EE0000' }} className="w-7 h-7 rounded flex items-center justify-center"><span className="text-white font-bold text-xs">WA</span></div>
                <span className="text-white font-bold">WA RFP Tracker</span>
              </div>
              <p className="text-gray-400 text-sm leading-relaxed max-w-sm">The most comprehensive source for Washington State government procurement opportunities. Updated daily from 25+ official sources.</p>
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
            <p className="text-gray-500 text-sm">Washington State RFP Tracker — Not affiliated with any government agency.</p>
            <p className="text-gray-600 text-xs">Updated daily at 7am Pacific</p>
          </div>
        </div>
      </footer>
    </div>
  )
}
