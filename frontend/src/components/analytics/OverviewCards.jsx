import React from "react"
import {
  Activity,
  CheckCircle2,
  Clock,
  Gauge,
  Package,
  TrendingUp,
  AlertTriangle,
  Zap,
} from "lucide-react"

export default function OverviewCards({ data, loading }) {
  const total = data?.total_searches ?? 0
  const success = data?.successful_searches ?? 0
  const successRate = data?.success_rate ?? 0.0
  const avgLat = data?.latency?.avg_seconds ?? 0.0
  const p50Lat = data?.latency?.p50_seconds ?? 0.0
  const p95Lat = data?.latency?.p95_seconds ?? 0.0
  const p99Lat = data?.latency?.p99_seconds ?? 0.0
  const totalProds = data?.total_products_discovered ?? 0

  const formatSeconds = (sec, ms) => {
    if (sec && sec >= 0.01) return `${sec >= 10 ? sec.toFixed(1) : sec.toFixed(2)}s`
    if (ms && ms > 0) return `${(ms / 1000.0).toFixed(3)}s`
    return "0.00s"
  }

  const cards = [
    {
      title: "Total Searches",
      value: total.toLocaleString(),
      subtitle: `${data?.zero_result_searches ?? 0} zero-result`,
      icon: Activity,
      color: "text-blue-600 bg-blue-50 border-blue-100",
    },
    {
      title: "Successful Searches",
      value: success.toLocaleString(),
      subtitle: `${data?.failed_searches ?? 0} failed / ${data?.timeout_searches ?? 0} timeouts`,
      icon: CheckCircle2,
      color: "text-emerald-600 bg-emerald-50 border-emerald-100",
    },
    {
      title: "Success Rate",
      value: `${successRate}%`,
      subtitle: `Supplier availability: ${data?.supplier_response_rate ?? 0}%`,
      icon: TrendingUp,
      color: successRate >= 90 ? "text-emerald-600 bg-emerald-50 border-emerald-100" : "text-amber-600 bg-amber-50 border-amber-100",
      highlight: true,
    },
    {
      title: "Average Latency",
      value: formatSeconds(avgLat, data?.latency?.avg_ms),
      subtitle: `${data?.latency?.avg_ms ?? 0} ms active`,
      icon: Clock,
      color: "text-indigo-600 bg-indigo-50 border-indigo-100",
    },
    {
      title: "P50 Latency (Median)",
      value: formatSeconds(p50Lat, data?.latency?.p50_ms),
      subtitle: "50% requests faster",
      icon: Gauge,
      color: "text-sky-600 bg-sky-50 border-sky-100",
    },
    {
      title: "P95 Latency",
      value: formatSeconds(p95Lat, data?.latency?.p95_ms),
      subtitle: "95% requests faster",
      icon: Gauge,
      color: "text-amber-600 bg-amber-50 border-amber-100",
    },
    {
      title: "P99 Latency (Tail)",
      value: formatSeconds(p99Lat, data?.latency?.p99_ms),
      subtitle: "99th percentile peak",
      icon: AlertTriangle,
      color: "text-rose-600 bg-rose-50 border-rose-100",
    },
    {
      title: "Products Discovered",
      value: totalProds.toLocaleString(),
      subtitle: `${data?.relevant_products_discovered ?? 0} relevant (${data?.product_discovery_rate ?? 0}% rate)`,
      icon: Package,
      color: "text-purple-600 bg-purple-50 border-purple-100",
    },
  ]

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4">
      {cards.map((c, i) => {
        const Icon = c.icon
        return (
          <div
            key={i}
            className={`bg-white rounded-xl border border-slate-200 p-4 shadow-xs flex flex-col justify-between transition-all hover:shadow-sm ${
              loading ? "opacity-60" : ""
            }`}
          >
            <div className="flex items-center justify-between gap-2 mb-2">
              <span className="text-xs font-medium text-slate-500 uppercase tracking-wider truncate">
                {c.title}
              </span>
              <div className={`p-2 rounded-lg border shrink-0 ${c.color}`}>
                <Icon className="w-4 h-4" />
              </div>
            </div>
            <div>
              <div className="text-2xl font-bold text-slate-900 tracking-tight">
                {c.value}
              </div>
              <div className="text-[11px] font-medium text-slate-500 mt-1 truncate">
                {c.subtitle}
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}
