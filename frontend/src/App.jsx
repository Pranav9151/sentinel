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
      const keys = ['status', 'morning', 'strategy', 'forex', 'guardrails']
      const paths = [
        '/api/status',
        '/api/morning-brief',
        '/api/strategy-factory',
        '/api/forex',
        '/api/guardrails',
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
            <span>Research Console</span>
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
            <p className="eyebrow">Project Sentinel v5</p>
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
        {killActive && <Banner tone="bad" icon={Lock}>{data.status?.kill_switch?.reason || 'Kill switch is active.'}</Banner>}

        <section className="content">
          {active === 'overview' && <Overview data={data} loading={loading} />}
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
          {active === 'settings' && <Readiness profile={profile} readiness={readiness} status={data.status} />}
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
      <div className="metric-grid">
        <Metric title="Trading Stage" value={profile?.trading_stage?.toUpperCase() || '-'} detail={profile?.paper_mode ? 'Paper protected' : 'Live gate active'} icon={Shield} tone="info" />
        <Metric title="Capital" value={money(profile?.total_portfolio_value_inr)} detail={`Max risk ${money(profile?.max_risk_per_trade_inr)}`} icon={Wallet} tone="good" />
        <Metric title="Market Bias" value={bias.bias || '-'} detail={`Score ${bias.score ?? '-'}`} icon={TrendingUp} tone={bias.bias?.includes('BEAR') ? 'bad' : 'good'} />
        <Metric title="Readiness" value={readiness?.ready ? 'READY' : `${blockerCount} Blockers`} detail="Production checks" icon={readiness?.ready ? CheckCircle2 : AlertTriangle} tone={readiness?.ready ? 'good' : 'warn'} />
      </div>

      <div className="layout-2">
        <Panel title="Morning Brief" action={loading ? 'Refreshing' : brief?.report_date}>
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
  return (
    <div className="stack">
      <div className="metric-grid">
        <Metric title="USD/INR" value={number(global.india_fx?.usd_inr, 2)} detail={pct(global.india_fx?.usd_inr_5d_change_pct)} icon={LineChart} tone="info" />
        <Metric title="US 10Y" value={`${number(global.rates?.us_10y_yield, 2)}%`} detail={`${number(global.rates?.us_10y_5d_change_bps, 0)} bps`} icon={Activity} tone="warn" />
        <Metric title="Gold" value={`$${number(global.commodities?.gold_usd, 0)}`} detail={pct(global.commodities?.gold_5d_change_pct, 1)} icon={TrendingUp} tone="good" />
        <Metric title="DXY Regime" value={overlay.dxy_regime || global.dxy?.regime || '-'} detail={pct(overlay.dxy_5d_change_pct)} icon={Gauge} tone="info" />
      </div>
      <div className="layout-2">
        <Panel title="Live FX Watchlist">
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

function Readiness({ profile, readiness, status }) {
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

function cleanText(value) {
  return String(value || '').replace(/[^\x20-\x7E]/g, '').replace(/\s+/g, ' ').trim()
}

createRoot(document.getElementById('root')).render(<Shell />)
