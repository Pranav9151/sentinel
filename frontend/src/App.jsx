import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Brain,
  CheckCircle2,
  Gauge,
  LineChart,
  Lock,
  RefreshCcw,
  Search,
  Settings,
  Shield,
  Target,
  TrendingUp,
  Wallet,
  XCircle,
} from 'lucide-react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import './styles.css'

const views = [
  { id: 'overview', label: 'Overview', icon: Gauge },
  { id: 'shortlist', label: 'Shortlist', icon: CheckCircle2 },
  { id: 'research', label: 'Research', icon: Brain },
  { id: 'options', label: 'Options Lab', icon: Activity },
  { id: 'screeners', label: 'Screeners', icon: Target },
  { id: 'strategy', label: 'Strategy Factory', icon: BarChart3 },
  { id: 'forex', label: 'Forex & Macro', icon: LineChart },
  { id: 'fundamentals', label: 'Fundamentals', icon: Search },
  { id: 'guardrails', label: 'Guardrails', icon: Shield },
  { id: 'settings', label: 'Readiness', icon: Settings },
]

const money = (value, digits = 0) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: digits,
  }).format(Number(value))
}

const number = (value, digits = 2) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  return Number(value).toLocaleString('en-IN', { maximumFractionDigits: digits })
}

const pct = (value, digits = 2) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  const n = Number(value)
  return `${n > 0 ? '+' : ''}${n.toFixed(digits)}%`
}

async function api(path) {
  const response = await fetch(path)
  const payload = await response.json()
  if (!response.ok) throw new Error(payload.error || `${path} returned ${response.status}`)
  return payload
}

function useSentinelData() {
  const [state, setState] = useState({ loading: true, error: '', data: {}, pending: {} })

  const refresh = async () => {
    setState((current) => ({ ...current, loading: true, error: '' }))
    try {
      const keys = ['status', 'morning', 'strategy', 'forex', 'guardrails', 'dataHealth']
      const paths = [
        '/api/status',
        '/api/morning-brief',
        '/api/strategy-factory',
        '/api/forex',
        '/api/guardrails',
        '/api/data-health',
      ]
      const results = await Promise.allSettled(paths.map((path) => api(path)))
      const data = {}
      const errors = []
      results.forEach((result, index) => {
        if (result.status === 'fulfilled') data[keys[index]] = result.value
        else errors.push(result.reason.message)
      })
      setState((current) => ({
        ...current,
        loading: false,
        error: errors.join(' | '),
        data: { ...current.data, ...data },
      }))
    } catch (error) {
      setState((current) => ({ ...current, loading: false, error: error.message }))
    }
  }

  const loadResource = async (key, path, force = false) => {
    if (!force && state.data[key]) return state.data[key]
    setState((current) => ({
      ...current,
      error: '',
      pending: { ...current.pending, [key]: true },
    }))
    try {
      const value = await api(path)
      setState((current) => ({
        ...current,
        data: { ...current.data, [key]: value },
        pending: { ...current.pending, [key]: false },
      }))
      return value
    } catch (error) {
      setState((current) => ({
        ...current,
        error: error.message,
        pending: { ...current.pending, [key]: false },
      }))
      return null
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  return { ...state, refresh, loadResource }
}

function Shell() {
  const [active, setActive] = useState('overview')
  const { loading, error, data, pending, refresh, loadResource } = useSentinelData()
  const profile = data.status?.profile
  const readiness = data.status?.readiness
  const killActive = Boolean(data.status?.kill_switch?.active)

  return (
    <div className="app">
      <aside className="rail">
        <div className="brand">
          <div className="brand-mark"><Shield size={21} /></div>
          <div>
            <h1>Sentinel</h1>
            <span>AI Trading Research Assistant</span>
          </div>
        </div>
        <nav className="nav">
          {views.map((view) => {
            const Icon = view.icon
            return (
              <button
                key={view.id}
                className={active === view.id ? 'nav-item active' : 'nav-item'}
                onClick={() => setActive(view.id)}
                title={view.label}
              >
                <Icon size={18} />
                <span>{view.label}</span>
              </button>
            )
          })}
        </nav>
        <div className="rail-footer">
          <StatusPill tone={killActive ? 'bad' : 'good'} icon={killActive ? XCircle : CheckCircle2}>
            {killActive ? 'Kill Active' : 'System Normal'}
          </StatusPill>
          <StatusPill tone={data.status?.mock_mode ? 'warn' : 'info'} icon={Activity}>
            {data.status?.mock_mode ? 'Mock Data' : 'Live Data'}
          </StatusPill>
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">AI Trading Research Assistant</p>
            <h2>{views.find((view) => view.id === active)?.label}</h2>
          </div>
          <div className="top-actions">
            <span className="timestamp">{data.status?.generated_at ? new Date(data.status.generated_at).toLocaleString() : 'Not loaded'}</span>
            <button className="icon-button" onClick={refresh} title="Refresh dashboard data">
              <RefreshCcw size={18} className={loading ? 'spin' : ''} />
            </button>
          </div>
        </header>

        {error && <Banner tone="bad" icon={AlertTriangle}>{error}</Banner>}
        {data.status?.mock_mode && (
          <Banner tone="warn" icon={AlertTriangle}>
            MOCK DATA ACTIVE: market, morning brief, and forex numbers are simulated test data, not live current prices.
          </Banner>
        )}
        {killActive && <Banner tone="bad" icon={Lock}>{data.status?.kill_switch?.reason || 'Kill switch is active.'}</Banner>}

        <section className="content">
          {active === 'overview' && <Overview data={data} loading={loading} />}
          {active === 'shortlist' && (
            <FinalShortlist
              shortlist={data.shortlist}
              loading={Boolean(pending.shortlist)}
              loadShortlist={(force = false) => loadResource('shortlist', '/api/final-shortlist', force)}
            />
          )}
          {active === 'research' && (
            <ResearchAssistant
              assets={data.researchAssets}
              loadingAssets={Boolean(pending.researchAssets)}
              loadAssets={() => loadResource('researchAssets', '/api/research-assets')}
            />
          )}
          {active === 'options' && <OptionsLab status={data.status} />}
          {active === 'screeners' && (
            <Screeners
              screeners={data.screeners}
              loading={Boolean(pending.screeners)}
              loadScreeners={(force = false) => loadResource('screeners', '/api/screeners', force)}
            />
          )}
          {active === 'strategy' && <StrategyFactory snapshot={data.strategy} />}
          {active === 'forex' && <ForexMacro forex={data.forex} morning={data.morning} />}
          {active === 'fundamentals' && (
            <Fundamentals
              symbols={data.status?.data?.equity_symbols || []}
              mockMode={Boolean(data.status?.mock_mode)}
            />
          )}
          {active === 'guardrails' && <Guardrails summary={data.guardrails} />}
          {active === 'settings' && <Readiness profile={profile} readiness={readiness} status={data.status} dataHealth={data.dataHealth} />}
        </section>
      </main>
    </div>
  )
}

function Overview({ data, loading }) {
  const profile = data.status?.profile
  const brief = data.morning
  const sections = brief?.sections || {}
  const bias = sections.bias || {}
  const internals = sections.internals || {}
  const fii = sections.fii_dii || {}
  const readiness = data.status?.readiness
  const blockerCount = readiness?.checks?.filter((check) => !check.passed).length || 0

  return (
    <div className="stack">
      {data.status?.data_quality?.warning && (
        <Banner tone={data.status.mock_mode ? 'warn' : 'info'} icon={AlertTriangle}>
          {data.status.data_quality.warning}
        </Banner>
      )}
      <div className="metric-grid">
        <Metric title="Trading Stage" value={profile?.trading_stage?.toUpperCase() || '-'} detail={profile?.paper_mode ? 'Paper protected' : 'Live gate active'} icon={Shield} tone="info" />
        <Metric title="Capital" value={money(profile?.total_portfolio_value_inr)} detail={`Max risk ${money(profile?.max_risk_per_trade_inr)}`} icon={Wallet} tone="good" />
        <Metric title="Market Bias" value={bias.bias || '-'} detail={`Score ${bias.score ?? '-'}`} icon={TrendingUp} tone={bias.bias?.includes('BEAR') ? 'bad' : 'good'} />
        <Metric title="Readiness" value={readiness?.ready ? 'READY' : `${blockerCount} Blockers`} detail="Production checks" icon={readiness?.ready ? CheckCircle2 : AlertTriangle} tone={readiness?.ready ? 'good' : 'warn'} />
      </div>

      <div className="layout-2">
        <Panel title="Morning Brief" action={loading ? 'Refreshing' : `${brief?.report_date || '-'} | ${brief?.data_quality?.mode || 'unknown'}`}>
          {!brief ? (
            <SkeletonGrid />
          ) : (
            <>
              <div className="brief-grid">
                <Stat label="Nifty 50" value={number(internals.nifty50_close, 0)} delta={pct(internals.nifty50_change_pct)} />
                <Stat label="India VIX" value={number(internals.india_vix, 1)} delta={internals.vix_label} />
                <Stat label="A/D Ratio" value={number(internals.advance_decline_ratio, 2)} delta={internals.ad_label} />
                <Stat label="FII Net" value={`${number(fii.fii_net_cr, 0)} Cr`} delta={fii.trend_20d?.trend} />
              </div>
              <div className="risk-list">
                {(sections.risk_flags || []).slice(0, 4).map((flag) => (
                  <div className="risk-item" key={flag}>
                    <AlertTriangle size={16} />
                    <span>{cleanText(flag)}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </Panel>

        <Panel title="Strategy Snapshot" action={data.strategy?.promotion_status || '-'}>
          {!data.strategy ? <SkeletonRows /> : (
            <div className="strategy-bars">
              {(data.strategy?.strategy_metrics || []).map((metric) => (
                <div className="strategy-row" key={metric.strategy_id}>
                  <div>
                    <strong>{metric.display_name.replace('Strategy ', 'S')}</strong>
                    <span>{metric.status}</span>
                  </div>
                  <div className="bar-track">
                    <div className="bar-fill" style={{ width: `${Math.min(Math.max(metric.oos_sharpe * 50, 8), 100)}%` }} />
                  </div>
                  <b>{number(metric.oos_sharpe, 2)}</b>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>
    </div>
  )
}

function FinalShortlist({ shortlist, loading, loadShortlist }) {
  useEffect(() => {
    if (!shortlist && !loading) loadShortlist(false)
  }, [shortlist, loading])

  const entries = shortlist?.entries || []
  return (
    <div className="stack">
      <div className="section-head">
        <div>
          <p className="eyebrow">Morning and live-market decision support</p>
          <h3>{loading ? 'Building Shortlist...' : shortlist?.title || 'Final Shortlist'}</h3>
        </div>
        <button className="primary-button" onClick={() => loadShortlist(true)} disabled={loading}>
          <RefreshCcw size={17} className={loading ? 'spin' : ''} />
          {loading ? 'Refreshing' : 'Refresh Shortlist'}
        </button>
      </div>

      {shortlist?.data_quality?.warning && (
        <Banner tone={shortlist.data_quality.mode === 'mock' ? 'warn' : 'info'} icon={AlertTriangle}>
          {shortlist.data_quality.warning}
        </Banner>
      )}
      {shortlist?.operator_warning && <Banner tone="warn" icon={Shield}>{shortlist.operator_warning}</Banner>}

      {loading && !entries.length && <ScreenerSkeleton />}
      <div className="shortlist-list">
        {entries.map((entry) => <ShortlistItem key={entry.symbol} entry={entry} />)}
        {!loading && !entries.length && <Empty label="No shortlist entries are available yet." />}
      </div>
    </div>
  )
}

function ShortlistItem({ entry }) {
  const avoid = entry.action === 'Avoid'
  const fields = [
    ['Action', entry.action],
    ['Entry Zone', avoid ? null : entry.entry_zone],
    ['Stop-Loss', avoid ? null : entry.stop_loss],
    ['Target 1', avoid ? null : entry.target_1],
    ['Target 2', avoid ? null : entry.target_2],
    ['Risk-Reward', avoid ? null : entry.risk_reward],
    ['Confidence', entry.confidence],
    ['Risk', entry.risk],
  ]
  return (
    <article className={`shortlist-item ${avoid ? 'avoid' : ''}`}>
      <div className="shortlist-head">
        <div className="rank">{entry.rank}</div>
        <div className="shortlist-title">
          <strong>{entry.symbol}</strong>
          <span>{entry.name} {entry.sector ? `| ${entry.sector}` : ''}</span>
        </div>
        <Badge tone={avoid ? 'bad' : riskTone(entry.risk)}>{entry.action}</Badge>
      </div>
      <div className="shortlist-grid">
        {fields.map(([label, value]) => (
          <div className="shortlist-field" key={label}>
            <span>{label}</span>
            <strong>{shortlistValue(label, value)}</strong>
          </div>
        ))}
      </div>
      <p className="shortlist-reason"><b>Reason:</b> {cleanText(entry.reason)}</p>
      {!!entry.warnings?.length && (
        <div className="candidate-meta">
          {entry.warnings.slice(0, 4).map((warning) => <span key={warning}>{cleanText(warning)}</span>)}
        </div>
      )}
    </article>
  )
}

function Screeners({ screeners, loading, loadScreeners }) {
  useEffect(() => {
    if (!screeners && !loading) loadScreeners(false)
  }, [screeners, loading])

  const entries = Object.entries(screeners || {}).filter(([key]) => !key.startsWith('_'))
  const total = screeners?._summary?.total_candidates || 0
  return (
    <div className="stack">
      <div className="section-head">
        <div>
          <p className="eyebrow">Seven-system funnel</p>
          <h3>{loading ? 'Scanning...' : `${total} Candidates`}</h3>
        </div>
        <button className="primary-button" onClick={() => loadScreeners(true)} disabled={loading}>
          <RefreshCcw size={17} className={loading ? 'spin' : ''} />
          {loading ? 'Running' : 'Run Screeners'}
        </button>
      </div>
      {loading && !entries.length && <ScreenerSkeleton />}
      <div className="screener-grid">
        {entries.map(([name, result]) => (
          <Panel key={name} title={labelize(name)} action={`${result.candidates?.length || 0} hits`}>
            <div className="candidate-list">
              {(result.candidates || []).slice(0, 5).map((candidate, index) => (
                <Candidate key={`${candidate.symbol}-${index}`} candidate={candidate} rank={index + 1} />
              ))}
              {!result.candidates?.length && <Empty label="No candidates cleared this screener." />}
            </div>
          </Panel>
        ))}
      </div>
    </div>
  )
}

function ResearchAssistant({ assets, loadingAssets, loadAssets }) {
  const [assetType, setAssetType] = useState('equity')
  const [symbol, setSymbol] = useState('RELIANCE')
  const [horizon, setHorizon] = useState('swing')
  const [capital, setCapital] = useState('')
  const [report, setReport] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!assets && !loadingAssets) loadAssets()
  }, [assets, loadingAssets])

  const options = assetOptions(assets, assetType)
  const runResearch = async () => {
    const cleanSymbol = symbol.trim().toUpperCase()
    setError('')
    if (!/^[A-Z0-9&_-]{2,32}$/.test(cleanSymbol)) {
      setError('Enter a valid supported symbol or fund code.')
      setReport(null)
      return
    }
    setLoading(true)
    try {
      const params = new URLSearchParams({
        asset_type: assetType,
        symbol: cleanSymbol,
        horizon,
      })
      if (capital.trim()) params.set('capital_inr', capital.trim())
      setReport(await api(`/api/research?${params.toString()}`))
    } catch (requestError) {
      setReport(null)
      setError(requestError.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="stack">
      <Panel title="Structured Research Assistant" action="A-J framework">
        <div className="research-form">
          <label>
            <span>Asset Type</span>
            <select value={assetType} onChange={(event) => {
              const next = event.target.value
              setAssetType(next)
              setSymbol(defaultSymbol(next))
              setReport(null)
              setError('')
            }}>
              <option value="equity">Equity</option>
              <option value="forex">Forex / Commodity</option>
              <option value="mutual_fund">Mutual Fund</option>
            </select>
          </label>
          <label>
            <span>Symbol</span>
            <input
              value={symbol}
              list={`research-${assetType}`}
              onChange={(event) => setSymbol(event.target.value.toUpperCase())}
              onKeyDown={(event) => {
                if (event.key === 'Enter') runResearch()
              }}
            />
            <datalist id={`research-${assetType}`}>
              {options.map((item) => (
                <option key={item.symbol} value={item.symbol}>{item.name}</option>
              ))}
            </datalist>
          </label>
          <label>
            <span>Horizon</span>
            <select value={horizon} onChange={(event) => setHorizon(event.target.value)}>
              <option value="intraday">Intraday</option>
              <option value="swing">Swing</option>
              <option value="positional">Positional</option>
              <option value="long-term">Long-term</option>
            </select>
          </label>
          <label>
            <span>Risk Capital INR</span>
            <input
              value={capital}
              inputMode="numeric"
              placeholder="Optional"
              onChange={(event) => setCapital(event.target.value.replace(/[^\d]/g, ''))}
            />
          </label>
          <button className="primary-button" onClick={runResearch} disabled={loading}>
            <Brain size={17} />
            {loading ? 'Researching' : 'Build Report'}
          </button>
        </div>
        {error && <div className="inline-error"><AlertTriangle size={16} />{error}</div>}
      </Panel>

      {loading && <ScreenerSkeleton />}
      {report && <ResearchReport report={report} />}
    </div>
  )
}

function ResearchReport({ report }) {
  const sections = Object.entries(report.sections || {})
  return (
    <div className="stack research-report">
      <Banner tone="warn" icon={Shield}>{report.operator_warning}</Banner>
      {sections.map(([title, content]) => (
        <Panel key={title} title={title}>
          <ResearchContent value={content} />
        </Panel>
      ))}
    </div>
  )
}

function OptionsLab({ status }) {
  const [form, setForm] = useState({
    underlying: 'RELIANCE',
    option_type: 'call',
    strike: '3100',
    expiry_days: '30',
    lot_size: '250',
    last_price: '42',
    implied_vol_pct: '22',
    underlying_price: '2950',
    holding_qty: '250',
    holding_avg: '2800',
    portfolio_value: String(status?.profile?.total_portfolio_value_inr || 300000),
    requested_lots: '1',
  })
  const [review, setReview] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const update = (key, value) => setForm((current) => ({ ...current, [key]: value }))
  const runReview = async () => {
    setError('')
    setLoading(true)
    try {
      const params = new URLSearchParams(form)
      setReview(await api(`/api/options-review?${params.toString()}`))
    } catch (requestError) {
      setReview(null)
      setError(requestError.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="stack">
      <Panel title="Covered-Call Options Lab" action="hedging-only">
        <div className="options-form">
          <Field label="Underlying" value={form.underlying} onChange={(value) => update('underlying', value.toUpperCase())} />
          <label>
            <span>Option Type</span>
            <select value={form.option_type} onChange={(event) => update('option_type', event.target.value)}>
              <option value="call">Call</option>
              <option value="put">Put</option>
            </select>
          </label>
          <Field label="Underlying Price" value={form.underlying_price} onChange={(value) => update('underlying_price', numericValue(value, true))} />
          <Field label="Strike" value={form.strike} onChange={(value) => update('strike', numericValue(value, true))} />
          <Field label="Days To Expiry" value={form.expiry_days} onChange={(value) => update('expiry_days', numericValue(value))} />
          <Field label="Lot Size" value={form.lot_size} onChange={(value) => update('lot_size', numericValue(value))} />
          <Field label="Option Price" value={form.last_price} onChange={(value) => update('last_price', numericValue(value, true))} />
          <Field label="IV %" value={form.implied_vol_pct} onChange={(value) => update('implied_vol_pct', numericValue(value, true))} />
          <Field label="Holding Qty" value={form.holding_qty} onChange={(value) => update('holding_qty', numericValue(value))} />
          <Field label="Holding Avg" value={form.holding_avg} onChange={(value) => update('holding_avg', numericValue(value, true))} />
          <Field label="Portfolio Value" value={form.portfolio_value} onChange={(value) => update('portfolio_value', numericValue(value, true))} />
          <Field label="Lots" value={form.requested_lots} onChange={(value) => update('requested_lots', numericValue(value))} />
          <button className="primary-button" onClick={runReview} disabled={loading}>
            <Activity size={17} />
            {loading ? 'Checking' : 'Review Setup'}
          </button>
        </div>
        {error && <div className="inline-error"><AlertTriangle size={16} />{error}</div>}
      </Panel>
      {review && <OptionsReview review={review} />}
    </div>
  )
}

function Field({ label, value, onChange }) {
  return (
    <label>
      <span>{label}</span>
      <input value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  )
}

function OptionsReview({ review }) {
  const greeks = review.greeks_snapshot?.greeks || {}
  const hedge = review.hedge_review || {}
  const payoff = review.payoff || {}
  const finalDecision = review.final_decision || {}
  const blocked = finalDecision.category === 'Blocked'
  return (
    <div className="stack">
      <Banner tone="warn" icon={Shield}>{review.operator_warning}</Banner>
      <div className="metric-grid">
        <Metric title="Decision" value={finalDecision.category || '-'} detail={blocked ? 'Do not execute' : 'Manual confirmation required'} icon={blocked ? XCircle : CheckCircle2} tone={blocked ? 'bad' : 'good'} />
        <Metric title="Delta" value={number(greeks.delta, 3)} detail={`Gamma ${number(greeks.gamma, 5)}`} icon={Activity} tone="info" />
        <Metric title="Theta / Day" value={number(greeks.theta, 2)} detail={`Vega ${number(greeks.vega, 2)}`} icon={Gauge} tone="warn" />
        <Metric title="Premium" value={payoff.available ? money(payoff.premium_income) : '-'} detail={hedge.status || '-'} icon={Wallet} tone="good" />
      </div>
      <div className="layout-2">
        <Panel title="Safety Gates">
          <div className="gate-grid">
            {(review.safety_gates || []).map((gate) => (
              <div className={gate.passed ? 'gate pass' : 'gate block'} key={gate.name}>
                {gate.passed ? <CheckCircle2 size={18} /> : <AlertTriangle size={18} />}
                <div><strong>{gate.name}</strong><span>{gate.detail}</span></div>
              </div>
            ))}
          </div>
        </Panel>
        <Panel title="Payoff Snapshot">
          <div className="research-kv">
            {payoff.available ? (
              Object.entries(payoff).map(([key, value]) => (
                <div className="research-kv-row" key={key}>
                  <span>{labelize(key)}</span>
                  <strong>{renderResearchValue(value)}</strong>
                </div>
              ))
            ) : (
              <Empty label={payoff.reason || 'Payoff unavailable.'} />
            )}
          </div>
        </Panel>
      </div>
      <Panel title="Final Explanation">
        <p className="research-text">{finalDecision.explanation}</p>
      </Panel>
    </div>
  )
}

function ResearchContent({ value }) {
  if (Array.isArray(value)) {
    return (
      <ul className="research-list">
        {value.map((item, index) => <li key={index}>{renderResearchValue(item)}</li>)}
      </ul>
    )
  }
  if (value && typeof value === 'object') {
    return (
      <div className="research-kv">
        {Object.entries(value).map(([key, item]) => (
          <div className="research-kv-row" key={key}>
            <span>{labelize(key)}</span>
            <strong>{renderResearchValue(item)}</strong>
          </div>
        ))}
      </div>
    )
  }
  return <p className="research-text">{renderResearchValue(value)}</p>
}

function renderResearchValue(value) {
  if (Array.isArray(value)) {
    if (!value.length) return 'None flagged'
    return value.map((item) => renderResearchValue(item)).join('; ')
  }
  if (value && typeof value === 'object') {
    return Object.entries(value)
      .map(([key, item]) => `${labelize(key)}: ${renderResearchValue(item)}`)
      .join(' | ')
  }
  if (value === true) return 'Yes'
  if (value === false) return 'No'
  if (value === null || value === undefined || value === '') return '-'
  return String(value)
}

function shortlistValue(label, value) {
  if (value === null || value === undefined || value === '') return '-'
  if (label === 'Entry Zone' && Array.isArray(value)) {
    return value.length >= 2 ? `${money(value[0])} to ${money(value[1])}` : money(value[0])
  }
  if (['Stop-Loss', 'Target 1', 'Target 2'].includes(label)) return money(value)
  if (label === 'Risk-Reward') return Number.isNaN(Number(value)) ? String(value) : `1:${number(value, 1)}`
  if (label === 'Confidence') return `${number(value, 1)}/10`
  return String(value)
}

function riskTone(risk) {
  const normalized = String(risk || '').toLowerCase()
  if (normalized.includes('very') || normalized.includes('high')) return 'bad'
  if (normalized.includes('medium')) return 'warn'
  return 'good'
}

function Candidate({ candidate, rank }) {
  const score = candidate.conviction_score ?? candidate.score ?? 0
  return (
    <div className="candidate">
      <div className="rank">{rank}</div>
      <div className="candidate-main">
        <div className="candidate-title">
          <strong>{candidate.symbol || candidate.pair || 'Unknown'}</strong>
          <span>{candidate.sector || candidate.direction || candidate.timeframe || 'Signal'}</span>
        </div>
        <div className="candidate-meta">
          <span>Score {number(score, 0)}</span>
          {candidate.rr_ratio && <span>R:R 1:{number(candidate.rr_ratio, 1)}</span>}
          {candidate.entry_low && <span>{money(candidate.entry_low)} - {money(candidate.entry_high)}</span>}
        </div>
      </div>
    </div>
  )
}

function StrategyFactory({ snapshot }) {
  const weights = Object.entries(snapshot?.target_weights || {}).map(([name, value]) => ({
    name: name.replace('strategy', 'S').replaceAll('_', ' '),
    value: Number((value * 100).toFixed(2)),
  }))
  const gates = snapshot?.gates || []

  return (
    <div className="stack">
      <div className="metric-grid">
        <Metric title="Promotion" value={snapshot?.promotion_status || '-'} detail={snapshot?.live_approved ? 'Live approved' : 'Research only'} icon={Brain} tone={snapshot?.live_approved ? 'good' : 'warn'} />
        <Metric title="Stage" value={snapshot?.stage?.toUpperCase() || '-'} detail="Operator profile" icon={Shield} tone="info" />
        <Metric title="Allocation" value={snapshot?.allocation_method || '-'} detail={`${weights.length} strategies`} icon={BarChart3} tone="good" />
        <Metric title="Blocked Gates" value={gates.filter((gate) => !gate.passed).length} detail="Must clear before live" icon={AlertTriangle} tone="warn" />
      </div>

      <div className="layout-2">
        <Panel title="Research Weights">
          <ResponsiveContainer width="100%" height={300}>
            <PieChart>
              <Pie data={weights} dataKey="value" nameKey="name" innerRadius={72} outerRadius={112} paddingAngle={3}>
                {weights.map((entry, index) => <Cell key={entry.name} fill={['#2dd4bf', '#60a5fa', '#fbbf24', '#f87171'][index % 4]} />)}
              </Pie>
              <Tooltip formatter={(value) => `${value}%`} />
            </PieChart>
          </ResponsiveContainer>
        </Panel>
        <Panel title="Strategy Metrics">
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Strategy</th><th>Sharpe</th><th>Return</th><th>Max DD</th><th>Gate</th></tr>
              </thead>
              <tbody>
                {(snapshot?.strategy_metrics || []).map((metric) => (
                  <tr key={metric.strategy_id}>
                    <td>{metric.display_name}</td>
                    <td>{number(metric.oos_sharpe, 2)}</td>
                    <td>{pct(metric.oos_total_return_pct)}</td>
                    <td>{pct(metric.oos_max_drawdown_pct)}</td>
                    <td><Badge tone={metric.research_gate_passed ? 'good' : 'bad'}>{metric.research_gate_passed ? 'PASS' : 'BLOCKED'}</Badge></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>

      <Panel title="Promotion Gates">
        <div className="gate-grid">
          {gates.map((gate) => (
            <div className={gate.passed ? 'gate pass' : 'gate block'} key={gate.name}>
              {gate.passed ? <CheckCircle2 size={18} /> : <XCircle size={18} />}
              <div><strong>{gate.name}</strong><span>{gate.detail}</span></div>
            </div>
          ))}
        </div>
      </Panel>
    </div>
  )
}

function ForexMacro({ forex, morning }) {
  const global = morning?.sections?.global || {}
  const rates = forex?.rates || []
  const overlay = forex?.overlay || {}
  const isMock = Boolean(forex?.data_quality?.mode === 'mock' || forex?.health?.mock_mode)
  return (
    <div className="stack">
      {forex?.data_quality?.warning && (
        <Banner tone={isMock ? 'warn' : 'info'} icon={AlertTriangle}>
          {forex.data_quality.warning}
        </Banner>
      )}
      <div className="metric-grid">
        <Metric title="USD/INR" value={number(global.india_fx?.usd_inr, 2)} detail={pct(global.india_fx?.usd_inr_5d_change_pct)} icon={LineChart} tone="info" />
        <Metric title="US 10Y" value={`${number(global.rates?.us_10y_yield, 2)}%`} detail={`${number(global.rates?.us_10y_5d_change_bps, 0)} bps`} icon={Activity} tone="warn" />
        <Metric title="Gold" value={`$${number(global.commodities?.gold_usd, 0)}`} detail={pct(global.commodities?.gold_5d_change_pct, 1)} icon={TrendingUp} tone="good" />
        <Metric title="DXY Regime" value={overlay.dxy_regime || global.dxy?.regime || '-'} detail={pct(overlay.dxy_5d_change_pct)} icon={Gauge} tone="info" />
      </div>
      <div className="layout-2">
        <Panel title={isMock ? 'Simulated FX Watchlist' : 'Live FX Watchlist'} action={forex?.data_quality?.mode || '-'}>
          <div className="rate-grid">
            {rates.map((rate) => (
              <div className="rate-tile" key={rate.pair}>
                <span>{rate.pair}</span>
                <strong>{rate.error ? 'Unavailable' : number(rate.mid, 4)}</strong>
                <Badge tone={rate.execution_eligible ? 'good' : 'warn'}>{rate.execution_eligible ? 'Executable' : 'Analysis only'}</Badge>
              </div>
            ))}
          </div>
        </Panel>
        <Panel title="Economic Calendar">
          <div className="event-list">
            {(forex?.calendar || []).map((event) => (
              <div className="event" key={`${event.timestamp}-${event.event}`}>
                <Badge tone={event.impact === 'CRITICAL' ? 'bad' : 'warn'}>{event.impact}</Badge>
                <div><strong>{event.event}</strong><span>{event.currency} | {new Date(event.timestamp).toLocaleString()}</span></div>
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </div>
  )
}

function Fundamentals({ symbols, mockMode }) {
  const [symbol, setSymbol] = useState('RELIANCE')
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const knownSymbols = new Set((symbols || []).map((item) => item.symbol))
  const load = async () => {
    const cleanSymbol = symbol.trim().toUpperCase()
    setError('')
    if (!/^[A-Z0-9&-]{2,20}$/.test(cleanSymbol)) {
      setData(null)
      setError('Enter a valid NSE symbol using letters/numbers only.')
      return
    }
    if (mockMode && knownSymbols.size > 0 && !knownSymbols.has(cleanSymbol)) {
      setData(null)
      setError(`${cleanSymbol} is not in the supported equity universe.`)
      return
    }
    setLoading(true)
    try {
      setData(await api(`/api/fundamentals?symbol=${encodeURIComponent(cleanSymbol)}`))
    } catch (requestError) {
      setData(null)
      setError(requestError.message)
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => {
    load()
  }, [])
  const quality = data?.quality
  const breakdown = Object.entries(quality?.breakdown || {}).map(([name, item]) => ({
    name: labelize(name),
    points: item.points,
    max: item.max,
  }))

  return (
    <div className="stack">
      <Panel title="Equity Quality">
        <div className="search-row">
          <input
            value={symbol}
            list="equity-symbols"
            onChange={(event) => setSymbol(event.target.value.toUpperCase())}
            onKeyDown={(event) => {
              if (event.key === 'Enter') load()
            }}
          />
          <datalist id="equity-symbols">
            {(symbols || []).map((item) => (
              <option key={item.symbol} value={item.symbol}>{item.name}</option>
            ))}
          </datalist>
          <button className="primary-button" onClick={load} disabled={loading}><Search size={17} />{loading ? 'Loading' : 'Analyze'}</button>
        </div>
        {error && <div className="inline-error"><AlertTriangle size={16} />{error}</div>}
      </Panel>
      {loading && !quality && <SkeletonGrid />}
      {quality && (
        <div className="layout-2">
          <Panel title={`${data.symbol} Scorecard`}>
            <div className="score-ring">
              <strong>{number(quality.quality_score, 1)}</strong>
              <span>Quality / 10</span>
            </div>
            <div className="brief-grid">
              <Stat label="Valuation" value={quality.valuation_score} delta="Score / 10" />
              <Stat label="ROE" value={`${number(quality.raw_data?.roe, 1)}%`} />
              <Stat label="Debt/Equity" value={number(quality.raw_data?.debt_equity, 2)} />
              <Stat label="Promoter" value={`${number(quality.raw_data?.promoter_holding, 1)}%`} />
            </div>
          </Panel>
          <Panel title="Quality Breakdown">
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={breakdown} layout="vertical">
                <CartesianGrid stroke="#263241" horizontal={false} />
                <XAxis type="number" stroke="#8190a5" />
                <YAxis dataKey="name" type="category" width={120} stroke="#8190a5" />
                <Tooltip />
                <Bar dataKey="points" fill="#2dd4bf" radius={[0, 6, 6, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </Panel>
        </div>
      )}
    </div>
  )
}

function Guardrails({ summary }) {
  return (
    <div className="stack">
      <div className="metric-grid">
        <Metric title="Override Status" value={summary?.status || '-'} detail="Three-override rule" icon={Shield} tone={summary?.demotion_triggered ? 'bad' : 'good'} />
        <Metric title="Overrides Used" value={summary?.overrides_30d ?? '-'} detail="Last 30 days" icon={AlertTriangle} tone="warn" />
        <Metric title="Remaining" value={summary?.overrides_remaining ?? '-'} detail={`Threshold ${summary?.threshold ?? '-'}`} icon={CheckCircle2} tone="good" />
      </div>
      <Panel title="Behavioral Protection">
        <div className="guardrail-copy">
          <Shield size={42} />
          <div>
            <h3>{summary?.demotion_triggered ? 'Paper-mode demotion active' : 'Discipline layer clear'}</h3>
            <p>{summary?.status || 'Guardrail summary is not loaded.'}</p>
          </div>
        </div>
      </Panel>
    </div>
  )
}

function Readiness({ profile, readiness, status, dataHealth }) {
  return (
    <div className="stack">
      <div className="metric-grid">
        <Metric title="Mode" value={status?.mock_mode ? 'MOCK' : 'LIVE'} detail="Data connector state" icon={Activity} tone={status?.mock_mode ? 'warn' : 'good'} />
        <Metric title="Stage" value={profile?.trading_stage?.toUpperCase() || '-'} detail="Operator config" icon={Shield} tone="info" />
        <Metric title="Emergency Fund" value={`${profile?.emergency_fund_months_confirmed ?? '-'} mo`} detail="Need 6 months" icon={Wallet} tone={(profile?.emergency_fund_months_confirmed || 0) >= 6 ? 'good' : 'warn'} />
        <Metric title="Data Store" value={status?.data?.symbols_with_ohlcv ?? '-'} detail="OHLCV symbols" icon={BarChart3} tone="good" />
      </div>
      <Panel title="Production Readiness Checks">
        <div className="table-wrap">
          <table>
            <thead><tr><th>Check</th><th>Category</th><th>Status</th><th>Detail</th></tr></thead>
            <tbody>
              {(readiness?.checks || []).map((check) => (
                <tr key={check.name}>
                  <td>{check.name}</td>
                  <td>{check.category}</td>
                  <td><Badge tone={check.passed ? 'good' : 'bad'}>{check.passed ? 'PASS' : 'BLOCKED'}</Badge></td>
                  <td>{check.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      <Panel title="Data Accuracy Gates" action={dataHealth?.overall_mode || status?.data_quality?.mode || '-'}>
        <div className="gate-grid">
          {(dataHealth?.checks || []).map((check) => (
            <div className={check.passed ? 'gate pass' : 'gate block'} key={check.name}>
              {check.passed ? <CheckCircle2 size={18} /> : <AlertTriangle size={18} />}
              <div><strong>{check.name}</strong><span>{check.status}: {check.detail}</span></div>
            </div>
          ))}
          {!dataHealth?.checks?.length && <Empty label="Data health has not loaded yet." />}
        </div>
      </Panel>
    </div>
  )
}

function Metric({ title, value, detail, icon: Icon, tone = 'info' }) {
  return (
    <div className={`metric ${tone}`}>
      <div className="metric-icon"><Icon size={19} /></div>
      <span>{title}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  )
}

function Panel({ title, action, children }) {
  return (
    <section className="panel">
      <div className="panel-head">
        <h3>{title}</h3>
        {action && <span>{action}</span>}
      </div>
      {children}
    </section>
  )
}

function Stat({ label, value, delta }) {
  return (
    <div className="stat">
      <span>{label}</span>
      <strong>{value}</strong>
      {delta && <small>{cleanText(delta)}</small>}
    </div>
  )
}

function Badge({ tone = 'info', children }) {
  return <span className={`badge ${tone}`}>{children}</span>
}

function StatusPill({ tone, icon: Icon, children }) {
  return (
    <div className={`status-pill ${tone}`}>
      <Icon size={15} />
      <span>{children}</span>
    </div>
  )
}

function Banner({ tone, icon: Icon, children }) {
  return (
    <div className={`banner ${tone}`}>
      <Icon size={18} />
      <span>{children}</span>
    </div>
  )
}

function Empty({ label }) {
  return <div className="empty">{label}</div>
}

function SkeletonGrid() {
  return (
    <div className="brief-grid">
      {[0, 1, 2, 3].map((item) => <div className="skeleton-card" key={item} />)}
    </div>
  )
}

function SkeletonRows() {
  return (
    <div className="skeleton-rows">
      {[0, 1, 2].map((item) => <div className="skeleton-row" key={item} />)}
    </div>
  )
}

function ScreenerSkeleton() {
  return (
    <div className="screener-grid">
      {[0, 1, 2, 3].map((item) => (
        <section className="panel" key={item}>
          <div className="panel-head"><div className="skeleton-title" /><div className="skeleton-chip" /></div>
          <SkeletonRows />
        </section>
      ))}
    </div>
  )
}

function labelize(value) {
  return String(value || '')
    .replace(/^s(\d)_/, 'S$1 ')
    .replaceAll('_', ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function assetOptions(assets, assetType) {
  if (!assets) return []
  if (assetType === 'equity') return assets.equities || []
  if (assetType === 'forex') return assets.forex || []
  if (assetType === 'mutual_fund') return assets.mutual_funds || []
  return []
}

function defaultSymbol(assetType) {
  return {
    equity: 'RELIANCE',
    forex: 'USDINR',
    mutual_fund: 'PPFAS_FLEXI',
  }[assetType] || 'RELIANCE'
}

function numericValue(value, allowDecimal = false) {
  const pattern = allowDecimal ? /[^\d.]/g : /[^\d]/g
  const cleaned = String(value || '').replace(pattern, '')
  if (!allowDecimal) return cleaned
  const parts = cleaned.split('.')
  return parts.length <= 1 ? cleaned : `${parts[0]}.${parts.slice(1).join('')}`
}

function cleanText(value) {
  return String(value || '').replace(/[^\x20-\x7E]/g, '').replace(/\s+/g, ' ').trim()
}

createRoot(document.getElementById('root')).render(<Shell />)
