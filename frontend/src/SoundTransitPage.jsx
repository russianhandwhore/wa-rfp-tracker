import { useState, useEffect, useCallback } from 'react'
import { createClient } from '@supabase/supabase-js'

const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL,
  import.meta.env.VITE_SUPABASE_ANON_KEY
)

const PORTAL_URL = 'https://www.biddingo.com/soundtransit'
const SNAPSHOT_URL = 'https://www.soundtransit.org/sites/default/files/documents/snapshot-current.pdf'

function SnapshotModal({ onClose }) {
  const [rfps, setRfps] = useState([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [sortBy, setSortBy] = useState('due_date_asc')
  const [lastFetched, setLastFetched] = useState(null)

  useEffect(() => {
    async function fetchSnapshot() {
      setLoading(true)
      try {
        const now = new Date().toISOString()
        const { data, error } = await supabase
          .from('rfps')
          .select('id,title,ref_number,due_date,posted_date,status,rfp_type,description,detail_url,raw_data')
          .eq('source_platform', 'Sound Transit')
          .or(`due_date.gte.${now},due_date.is.null`)
          .order('due_date', { ascending: true, nullsFirst: false })
          .limit(200)
        if (!error && data) {
          setRfps(data)
          setLastFetched(new Date().toLocaleString())
        }
      } catch (e) {
        console.error('Snapshot fetch error:', e)
      }
      setLoading(false)
    }
    fetchSnapshot()
  }, [])

  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  const getPhaseLabel = (rfp) => {
    try {
      const raw = typeof rfp.raw_data === 'string' ? JSON.parse(rfp.raw_data) : (rfp.raw_data || {})
      return raw.phase_label || null
    } catch { return null }
  }

  const filtered = rfps
    .filter(r => !search || r.title?.toLowerCase().includes(search.toLowerCase()) || r.ref_number?.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => {
      if (sortBy === 'due_date_asc') return (a.due_date || 'zzz').localeCompare(b.due_date || 'zzz')
      if (sortBy === 'due_date_desc') return (b.due_date || '').localeCompare(a.due_date || '')
      if (sortBy === 'title_asc') return (a.title || '').localeCompare(b.title || '')
      return 0
    })

  const formatDate = (d) => {
    if (!d) return 'TBD'
    return new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
  }

  const getDaysLeft = (d) => {
    if (!d) return null
    return Math.ceil((new Date(d) - new Date()) / (1000 * 60 * 60 * 24))
  }

  const phaseColor = (phase) => {
    if (!phase) return 'bg-gray-100 text-gray-600'
    if (phase === 'Advertising') return 'bg-green-50 text-green-700 border border-green-200'
    if (phase === 'Upcoming') return 'bg-blue-50 text-blue-700 border border-blue-200'
    if (phase === 'Evaluating') return 'bg-yellow-50 text-yellow-700 border border-yellow-200'
    return 'bg-gray-100 text-gray-600'
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ backgroundColor: 'rgba(0,0,0,0.7)' }} onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-4xl flex flex-col" style={{ maxHeight: '90vh' }} onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-gray-100 flex-shrink-0">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-blue-50 text-blue-700 border border-blue-200">📋 Snapshot</span>
              <span className="text-xs text-gray-400">From biweekly PDF — $250K+ opportunities</span>
            </div>
            <h2 className="font-bold text-gray-900 text-xl">Sound Transit Procurement Snapshot</h2>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 transition-colors flex-shrink-0 ml-4">
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
          </button>
        </div>

        {/* Search & Sort */}
        <div className="flex gap-3 p-4 border-b border-gray-100 flex-shrink-0">
          <div className="flex-1 relative">
            <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" /></svg>
            <input
              type="text"
              placeholder="Search by title or ref number..."
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="w-full pl-9 pr-4 py-2 text-sm rounded-lg border border-gray-300 focus:outline-none focus:border-red-500"
            />
          </div>
          <select
            value={sortBy}
            onChange={e => setSortBy(e.target.value)}
            className="px-3 py-2 text-sm rounded-lg border border-gray-300 focus:outline-none focus:border-red-500 bg-white"
          >
            <option value="due_date_asc">Due Soonest</option>
            <option value="due_date_desc">Due Latest</option>
            <option value="title_asc">Title A–Z</option>
          </select>
        </div>

        {/* List */}
        <div className="overflow-y-auto flex-1 p-4">
          {loading ? (
            <div className="flex items-center justify-center py-16">
              <div className="w-8 h-8 border-4 border-red-500 border-t-transparent rounded-full animate-spin"></div>
            </div>
          ) : filtered.length === 0 ? (
            <div className="text-center py-16">
              <div className="text-4xl mb-3">🔍</div>
              <p className="text-gray-500 text-sm">{search ? 'No results for your search' : 'No snapshot data found'}</p>
            </div>
          ) : (
            <div className="space-y-3">
              {filtered.map(rfp => {
                const phase = getPhaseLabel(rfp)
                const daysLeft = getDaysLeft(rfp.due_date)
                return (
                  <div key={rfp.id} className="bg-white border border-gray-200 rounded-xl p-4 hover:border-red-200 hover:shadow-sm transition-all">
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                          {phase && <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${phaseColor(phase)}`}>{phase === 'Advertising' ? '📢' : phase === 'Upcoming' ? '🔮' : '⏳'} {phase}</span>}
                          {rfp.rfp_type && <span className="text-xs text-gray-500 px-2 py-0.5 rounded-full bg-gray-100">{rfp.rfp_type}</span>}
                        </div>
                        <h3 className="font-semibold text-gray-900 text-sm leading-snug mb-1">{rfp.title}</h3>
                        {rfp.description && <p className="text-xs text-gray-500 leading-relaxed line-clamp-2 mb-1.5">{rfp.description}</p>}
                        {rfp.ref_number && <span className="text-xs text-gray-400"><span className="font-medium text-gray-500">Ref:</span> {rfp.ref_number}</span>}
                      </div>
                      <div className="flex flex-col items-end gap-2 flex-shrink-0">
                        <div className="text-right">
                          <div className="text-xs text-gray-400 uppercase tracking-wide">Due</div>
                          <div className="text-sm font-bold text-gray-800">{formatDate(rfp.due_date)}</div>
                          {daysLeft !== null && (
                            <div className={`text-xs ${daysLeft <= 3 ? 'text-red-500 font-bold' : daysLeft <= 7 ? 'text-orange-500' : 'text-gray-500'}`}>
                              {daysLeft < 0 ? 'Expired' : daysLeft === 0 ? 'Due today!' : `${daysLeft} days`}
                            </div>
                          )}
                        </div>
                        <a
                          href={rfp.detail_url || PORTAL_URL}
                          target="_blank"
                          rel="noopener noreferrer"
                          style={{ backgroundColor: '#EE0000' }}
                          className="text-white text-xs font-semibold px-3 py-1.5 rounded-lg hover:opacity-90 transition-opacity whitespace-nowrap"
                        >
                          View →
                        </a>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-gray-100 bg-gray-50 rounded-b-2xl flex items-center justify-between flex-shrink-0">
          <p className="text-xs text-gray-400">
            {lastFetched ? `Loaded ${lastFetched}` : ''} · Biweekly snapshot, $250K+ only ·{' '}
            <a href={SNAPSHOT_URL} target="_blank" rel="noopener noreferrer" className="text-red-500 hover:underline">Download PDF</a>
          </p>
          <span className="text-xs text-gray-500 font-medium">{filtered.length} item{filtered.length !== 1 ? 's' : ''}</span>
        </div>
      </div>
    </div>
  )
}

export default function SoundTransitPage({ onBack }) {
  const [showModal, setShowModal] = useState(false)
  const [iframeError, setIframeError] = useState(false)
  const [iframeLoaded, setIframeLoaded] = useState(false)

  // Detect iframe block via timeout — onError doesn't fire for X-Frame-Options blocks
  useEffect(() => {
    if (iframeError) return
    const timer = setTimeout(() => {
      if (!iframeLoaded) setIframeError(true)
    }, 12000)
    return () => clearTimeout(timer)
  }, [iframeLoaded, iframeError])

  const handleIframeLoad = () => setIframeLoaded(true)
  const closeModal = useCallback(() => setShowModal(false), [])

  return (
    <div className="min-h-screen bg-gray-50">
      {showModal && <SnapshotModal onClose={closeModal} />}

      {/* Header */}
      <div style={{ backgroundColor: '#151515' }} className="border-b border-gray-800">
        <div className="max-w-7xl mx-auto px-4 py-4 flex items-center gap-4">
          <button onClick={onBack} className="text-gray-400 hover:text-white transition-colors flex items-center gap-1.5 text-sm">
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" /></svg>
            Back
          </button>
          <div className="flex items-center gap-3">
            <div style={{ backgroundColor: '#EE0000' }} className="w-7 h-7 rounded flex items-center justify-center flex-shrink-0">
              <span className="text-white font-bold text-xs">ST</span>
            </div>
            <span className="text-white font-bold">Sound Transit Procurement</span>
          </div>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-4 py-8">
        {/* Info banner */}
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 mb-6 flex items-start gap-3">
          <span className="text-amber-500 text-lg flex-shrink-0">⚠️</span>
          <div>
            <p className="text-sm font-semibold text-amber-800 mb-0.5">Document downloads may require free vendor sign-in</p>
            <p className="text-xs text-amber-700">
              You can browse all active solicitations below. To download bid documents, register for free at the vendor portal.{' '}
              <a href={PORTAL_URL} target="_blank" rel="noopener noreferrer" className="underline font-medium">Register here →</a>
            </p>
          </div>
        </div>

        {/* Action buttons */}
        <div className="flex flex-wrap gap-3 mb-8">
          <button
            onClick={() => setShowModal(true)}
            style={{ backgroundColor: '#EE0000' }}
            className="text-white font-semibold px-6 py-3 rounded-lg hover:opacity-90 transition-opacity flex items-center gap-2"
          >
            <span>📋</span>
            View Snapshot Listings
          </button>
          <a
            href={PORTAL_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="font-semibold px-6 py-3 rounded-lg border-2 border-gray-300 hover:border-red-400 hover:text-red-600 bg-white transition-all flex items-center gap-2 text-gray-700"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" /></svg>
            Open Vendor Portal in New Tab
          </a>
          <a
            href={SNAPSHOT_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="font-semibold px-6 py-3 rounded-lg border-2 border-gray-300 hover:border-red-400 hover:text-red-600 bg-white transition-all flex items-center gap-2 text-gray-700"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
            Download Snapshot PDF
          </a>
        </div>

        {/* Iframe section */}
        <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden shadow-sm">
          <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
            <div>
              <h2 className="font-bold text-gray-900">Live Vendor Portal</h2>
              <p className="text-xs text-gray-500 mt-0.5">Browse all active Sound Transit solicitations</p>
            </div>
            <a
              href={PORTAL_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs font-semibold text-gray-500 hover:text-red-600 flex items-center gap-1 transition-colors"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" /></svg>
              Open in New Tab
            </a>
          </div>

          {iframeError ? (
            <div className="flex flex-col items-center justify-center py-20 text-center px-6">
              <div className="text-5xl mb-4">🚧</div>
              <h3 className="font-semibold text-gray-800 text-lg mb-2">Portal couldn't load here</h3>
              <p className="text-gray-500 text-sm mb-6 max-w-sm">The vendor portal has security settings that prevent it from loading inside this page. Open it directly instead.</p>
              <a
                href={PORTAL_URL}
                target="_blank"
                rel="noopener noreferrer"
                style={{ backgroundColor: '#EE0000' }}
                className="text-white font-semibold px-6 py-3 rounded-lg hover:opacity-90 transition-opacity"
              >
                Open Vendor Portal →
              </a>
            </div>
          ) : (
            <div className="relative" style={{ height: '1000px' }}>
              {!iframeLoaded && (
                <div className="absolute inset-0 flex items-center justify-center bg-gray-50 z-10">
                  <div className="text-center">
                    <div className="w-8 h-8 border-4 border-red-500 border-t-transparent rounded-full animate-spin mx-auto mb-3"></div>
                    <p className="text-sm text-gray-500">Loading vendor portal...</p>
                  </div>
                </div>
              )}
              <iframe
                src={PORTAL_URL}
                title="Sound Transit Vendor Portal"
                className="w-full h-full border-0"
                onLoad={handleIframeLoad}
                sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox"
                referrerPolicy="no-referrer-when-downgrade"
              />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
