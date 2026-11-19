import React, { useState } from "react"
import { Clock, BarChart3, TrendingUp, Layers, HelpCircle } from "lucide-react"

export default function LatencyCharts({ latencyData, historyData, loading }) {
  const [hoveredPoint, setHoveredPoint] = useState(null)

  const summary = latencyData?.summary || {}
  const histogram = latencyData?.histogram || []
  const stageBreakdown = latencyData?.stage_breakdown || []
  const timeline = historyData?.timeline || []

  // Max histogram percentage for scaling
  const maxBucketPct = Math.max(...histogram.map((b) => b.percentage || 0), 1)

  // Max stage duration for scaling
  const maxStageSec = Math.max(...stageBreakdown.map((s) => s.avg_seconds || 0), 0.1)

  // SVG Trend Chart Dimensions
  const chartWidth = 650
  const chartHeight = 180
  const paddingX = 40
  const paddingY = 25

  const trendPoints = timeline.map((t, idx) => ({
    x: paddingX + (idx / Math.max(1, timeline.length - 1)) * (chartWidth - paddingX * 2),
    y: t.avg_latency_seconds,
    data: t,
  }))

  const maxLatency = Math.max(...timeline.map((t) => t.avg_latency_seconds || 0), 1)
  const getY = (val) =>
    chartHeight - paddingY - (val / maxLatency) * (chartHeight - paddingY * 2)

  const svgPath = trendPoints.length > 1
    ? trendPoints.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x.toFixed(1)} ${getY(p.y).toFixed(1)}`).join(" ")
    : ""

  const areaPath = trendPoints.length > 1
    ? `${svgPath} L ${trendPoints[trendPoints.length - 1].x.toFixed(1)} ${chartHeight - paddingY} L ${trendPoints[0].x.toFixed(1)} ${chartHeight - paddingY} Z`
    : ""

  return (
    <div className="space-y-4">
      {/* Top Row: Percentile Summary & Latency Histogram */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
        {/* Percentile Metric Highlights (5 cols) */}
        <div className="lg:col-span-5 bg-white border border-slate-200 rounded-xl p-4 shadow-xs flex flex-col justify-between">
          <div className="flex items-center justify-between pb-3 border-b border-slate-100">
            <div className="flex items-center gap-2">
              <Clock className="w-4 h-4 text-blue-600" />
              <h3 className="font-semibold text-slate-900 text-sm">Latency Percentiles</h3>
            </div>
            <span className="text-[11px] font-medium text-slate-500 bg-slate-100 px-2 py-0.5 rounded-full" title="Calculated using NIST Type 7 linear interpolation">
              Linear Interpolated
            </span>
          </div>

          <div className="grid grid-cols-3 gap-2 my-4">
            <div className="bg-slate-50 border border-slate-200/80 rounded-lg p-2.5 text-center">
              <div className="text-[10px] uppercase font-bold text-slate-500 tracking-wider">P50 (Median)</div>
              <div className="text-xl font-bold text-slate-900 mt-0.5">{summary.p50_seconds ?? 0}s</div>
              <div className="text-[10px] text-slate-400">{summary.p50_ms ?? 0} ms</div>
            </div>
            <div className="bg-amber-50/60 border border-amber-200/80 rounded-lg p-2.5 text-center">
              <div className="text-[10px] uppercase font-bold text-amber-700 tracking-wider">P95 (Target)</div>
              <div className="text-xl font-bold text-amber-900 mt-0.5">{summary.p95_seconds ?? 0}s</div>
              <div className="text-[10px] text-amber-600">{summary.p95_ms ?? 0} ms</div>
            </div>
            <div className="bg-rose-50/60 border border-rose-200/80 rounded-lg p-2.5 text-center">
              <div className="text-[10px] uppercase font-bold text-rose-700 tracking-wider">P99 (Tail)</div>
              <div className="text-xl font-bold text-rose-900 mt-0.5">{summary.p99_seconds ?? 0}s</div>
              <div className="text-[10px] text-rose-600">{summary.p99_ms ?? 0} ms</div>
            </div>
          </div>

          <div className="space-y-2 pt-2 border-t border-slate-100 text-xs">
            <div className="flex items-center justify-between text-slate-600">
              <span className="font-medium">Average Latency:</span>
              <span className="font-semibold text-slate-900">{summary.avg_seconds ?? 0}s ({summary.avg_ms ?? 0} ms)</span>
            </div>
            <div className="flex items-center justify-between text-slate-600">
              <span className="font-medium">Minimum Latency:</span>
              <span className="font-semibold text-slate-900">{summary.min_seconds ?? 0}s ({summary.min_ms ?? 0} ms)</span>
            </div>
            <div className="flex items-center justify-between text-slate-600">
              <span className="font-medium">Maximum Latency:</span>
              <span className="font-semibold text-slate-900">{summary.max_seconds ?? 0}s ({summary.max_ms ?? 0} ms)</span>
            </div>
          </div>
        </div>

        {/* Latency Distribution Histogram (7 cols) */}
        <div className="lg:col-span-7 bg-white border border-slate-200 rounded-xl p-4 shadow-xs flex flex-col justify-between">
          <div className="flex items-center justify-between pb-2 border-b border-slate-100 mb-2">
            <div className="flex items-center gap-2">
              <BarChart3 className="w-4 h-4 text-indigo-600" />
              <h3 className="font-semibold text-slate-900 text-sm">Latency Distribution</h3>
            </div>
            <span className="text-[11px] text-slate-500">
              {histogram.reduce((acc, b) => acc + (b.count || 0), 0)} recorded searches
            </span>
          </div>

          <div className="space-y-2 my-auto">
            {histogram.map((b, idx) => (
              <div key={idx} className="flex items-center gap-3 text-xs">
                <span className="w-16 font-medium text-slate-600 shrink-0 text-right">{b.label}</span>
                <div className="flex-1 bg-slate-100 rounded-full h-3 overflow-hidden">
                  <div
                    className="bg-indigo-600 h-full rounded-full transition-all duration-500"
                    style={{
                      width: `${Math.max(b.percentage > 0 ? 3 : 0, (b.percentage / maxBucketPct) * 100)}%`,
                    }}
                  />
                </div>
                <div className="w-20 flex justify-between shrink-0 font-medium text-slate-700">
                  <span>{b.count}</span>
                  <span className="text-slate-400">({b.percentage}%)</span>
                </div>
              </div>
            ))}
          </div>

          <p className="text-[11px] text-slate-400 pt-2 border-t border-slate-100 mt-2">
            Fast sub-second cached queries resolve in &lt;1s; full dual-supplier live scrapers typically take 3s–8s.
          </p>
        </div>
      </div>

      {/* Middle Row: Historical Latency Trend Chart */}
      <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-xs">
        <div className="flex items-center justify-between pb-2 border-b border-slate-100 mb-2">
          <div className="flex items-center gap-2">
            <TrendingUp className="w-4 h-4 text-emerald-600" />
            <h3 className="font-semibold text-slate-900 text-sm">Historical Latency Trends</h3>
          </div>
          <span className="text-[11px] text-slate-500">Average response time over time</span>
        </div>

        {timeline.length === 0 ? (
          <div className="py-12 text-center text-sm text-slate-400">
            No historical latency data recorded in this time range.
          </div>
        ) : (
          <div className="relative overflow-x-auto">
            <svg
              viewBox={`0 0 ${chartWidth} ${chartHeight}`}
              className="w-full h-44 select-none"
            >
              <defs>
                <linearGradient id="latencyGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#10b981" stopOpacity="0.25" />
                  <stop offset="100%" stopColor="#10b981" stopOpacity="0.0" />
                </linearGradient>
              </defs>

              {/* Grid Lines */}
              {[0, 0.25, 0.5, 0.75, 1].map((pct, idx) => {
                const y = paddingY + pct * (chartHeight - paddingY * 2)
                const val = (maxLatency * (1 - pct)).toFixed(1)
                return (
                  <g key={idx}>
                    <line
                      x1={paddingX}
                      y1={y}
                      x2={chartWidth - paddingX}
                      y2={y}
                      stroke="#f1f5f9"
                      strokeWidth="1"
                    />
                    <text
                      x={paddingX - 6}
                      y={y + 3}
                      fill="#94a3b8"
                      fontSize="9"
                      textAnchor="end"
                    >
                      {val}s
                    </text>
                  </g>
                )
              })}

              {/* Area & Line */}
              {areaPath && <path d={areaPath} fill="url(#latencyGradient)" />}
              {svgPath && (
                <path
                  d={svgPath}
                  fill="none"
                  stroke="#10b981"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              )}

              {/* Interactive Data Points */}
              {trendPoints.map((p, idx) => (
                <circle
                  key={idx}
                  cx={p.x}
                  cy={getY(p.y)}
                  r="4"
                  fill="#ffffff"
                  stroke="#10b981"
                  strokeWidth="2"
                  className="cursor-pointer transition-transform hover:scale-150"
                  onMouseEnter={() => setHoveredPoint(p)}
                  onMouseLeave={() => setHoveredPoint(null)}
                />
              ))}
            </svg>

            {/* Hover Tooltip */}
            {hoveredPoint && (
              <div
                className="absolute bg-slate-900 text-white text-[11px] rounded-lg px-2.5 py-1.5 shadow-lg pointer-events-none -translate-x-1/2 -translate-y-full"
                style={{
                  left: `${(hoveredPoint.x / chartWidth) * 100}%`,
                  top: `${(getY(hoveredPoint.y) / chartHeight) * 100 - 8}%`,
                }}
              >
                <div className="font-semibold">{hoveredPoint.data.bucket}</div>
                <div className="text-emerald-400">Avg: {hoveredPoint.data.avg_latency_seconds}s</div>
                <div className="text-slate-300">{hoveredPoint.data.total_searches} searches</div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Bottom Row: 10-Stage Component Duration Breakdown */}
      <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-xs">
        <div className="flex items-center justify-between pb-3 border-b border-slate-100 mb-3">
          <div className="flex items-center gap-2">
            <Layers className="w-4 h-4 text-purple-600" />
            <h3 className="font-semibold text-slate-900 text-sm">Stage-Level Component Latency Breakdown</h3>
          </div>
          <span className="text-[11px] text-slate-500">
            10-Stage Workflow Duration
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-3">
          {stageBreakdown.map((s, idx) => (
            <div key={idx} className="space-y-1">
              <div className="flex items-center justify-between text-xs">
                <span className="font-medium text-slate-700 truncate" title={s.label}>
                  {idx + 1}. {s.label}
                </span>
                <span className="font-bold text-slate-900">
                  {s.avg_seconds}s <span className="text-[10px] text-slate-400 font-normal">({s.avg_ms}ms)</span>
                </span>
              </div>
              <div className="bg-slate-100 h-2 rounded-full overflow-hidden">
                <div
                  className="bg-purple-600 h-full rounded-full transition-all duration-500"
                  style={{
                    width: `${Math.max(s.avg_seconds > 0 ? 2 : 0, (s.avg_seconds / maxStageSec) * 100)}%`,
                  }}
                />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
