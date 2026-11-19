import React, { useState, useEffect, useCallback } from "react"
import {
  Activity,
  Clock,
  Server,
  ShieldCheck,
  List,
  Zap,
  RefreshCw,
  Calendar,
  Filter,
  AlertTriangle,
  PlayCircle,
  Trash2,
} from "lucide-react"

import OverviewCards from "./OverviewCards"
import LatencyCharts from "./LatencyCharts"
import SupplierComparison from "./SupplierComparison"
import SearchQuality from "./SearchQuality"
import RequestHistory from "./RequestHistory"
import BenchmarkPanel from "./BenchmarkPanel"

import {
  fetchMetricsOverview,
  fetchMetricsLatency,
  fetchMetricsSuppliers,
  fetchMetricsQuality,
  fetchMetricsHistory,
  fetchMetricsRequests,
  clearMetricsData,
} from "../../services/api"

export default function AnalyticsDashboard() {
  const [activeTab, setActiveTab] = useState("overview") // "overview" | "suppliers" | "quality" | "requests" | "benchmark"
  const [timeRange, setTimeRange] = useState("all") // "24h" | "7d" | "30d" | "all"
  const [includeBenchmark, setIncludeBenchmark] = useState(false)
  const [autoRefresh, setAutoRefresh] = useState(false)
  const [loading, setLoading] = useState(true)
  const [clearing, setClearing] = useState(false)
  const [errorMsg, setErrorMsg] = useState(null)

  // Datasets
  const [overviewData, setOverviewData] = useState(null)
  const [latencyData, setLatencyData] = useState(null)
  const [supplierData, setSupplierData] = useState(null)
  const [qualityData, setQualityData] = useState(null)
  const [historyData, setHistoryData] = useState(null)
  const [requestsData, setRequestsData] = useState({ requests: [], total: 0 })
  const [requestStatusFilter, setRequestStatusFilter] = useState("all")
  const [requestSearchQuery, setRequestSearchQuery] = useState("")

  const loadAllMetrics = useCallback(async () => {
    setLoading(true)
    setErrorMsg(null)
    try {
      const [ovRes, latRes, supRes, qualRes, histRes, reqsRes] = await Promise.allSettled([
        fetchMetricsOverview({ timeRange, includeBenchmark }),
        fetchMetricsLatency({ timeRange, includeBenchmark }),
        fetchMetricsSuppliers({ timeRange, includeBenchmark }),
        fetchMetricsQuality({ timeRange, includeBenchmark }),
        fetchMetricsHistory({ timeRange, includeBenchmark, granularity: timeRange === "24h" ? "hour" : "day" }),
        fetchMetricsRequests({
          timeRange,
          includeBenchmark,
          status: requestStatusFilter,
          query: requestSearchQuery,
          limit: 50,
        }),
      ])

      if (ovRes.status === "fulfilled") setOverviewData(ovRes.value)
      if (latRes.status === "fulfilled") setLatencyData(latRes.value)
      if (supRes.status === "fulfilled") setSupplierData(supRes.value)
      if (qualRes.status === "fulfilled") setQualityData(qualRes.value)
      if (histRes.status === "fulfilled") setHistoryData(histRes.value)
      if (reqsRes.status === "fulfilled") setRequestsData(reqsRes.value)

      const rejections = [ovRes, latRes, supRes, qualRes, histRes, reqsRes].filter((r) => r.status === "rejected")
      if (rejections.length === 6) {
        setErrorMsg("Unable to connect to the backend metrics API. Please ensure the backend server is running on port 8000.")
      } else if (rejections.length > 0) {
        console.warn("Some metrics endpoints failed to load:", rejections)
      }
    } catch (err) {
      console.error("Failed to load analytics dashboard data:", err)
      setErrorMsg("Error loading metrics: " + (err.message || String(err)))
    } finally {
      setLoading(false)
    }
  }, [timeRange, includeBenchmark, requestStatusFilter, requestSearchQuery])

  useEffect(() => {
    loadAllMetrics()
  }, [loadAllMetrics])

  useEffect(() => {
    if (!autoRefresh) return
    const interval = setInterval(loadAllMetrics, 10000)
    return () => clearInterval(interval)
  }, [autoRefresh, loadAllMetrics])

  const handleClearMetrics = async () => {
    const confirmed = window.confirm(
      "Are you sure you want to clear all dashboard metrics and search history? This action cannot be undone."
    )
    if (!confirmed) return

    setClearing(true)
    try {
      await clearMetricsData({ includeBenchmark: true })
      await loadAllMetrics()
    } catch (err) {
      console.error("Failed to clear metrics:", err)
      setErrorMsg("Failed to clear dashboard data: " + (err.message || String(err)))
    } finally {
      setClearing(false)
    }
  }

  const tabs = [
    { id: "overview", label: "Overview & Latency", icon: Clock },
    { id: "suppliers", label: "Supplier Benchmarking", icon: Server },
    { id: "quality", label: "Search Quality & Discovery", icon: ShieldCheck },
    { id: "requests", label: "Request Explorer", icon: List },
    { id: "benchmark", label: "Load Test Runner", icon: Zap },
  ]

  return (
    <div className="flex-1 h-full min-h-0 flex flex-col overflow-hidden bg-slate-50 text-slate-900">
      {/* Top Filter and Actions Bar */}
      <div className="shrink-0 bg-white border-b border-slate-200 px-4 py-3 sm:px-6 shadow-2xs">
        <div className="flex flex-col lg:flex-row items-start lg:items-center justify-between gap-3 max-w-7xl mx-auto">
          {/* Title & Badge */}
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-indigo-600 text-white flex items-center justify-center shadow-xs">
              <Activity className="w-4 h-4" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="font-bold text-slate-900 text-base tracking-tight">
                  Performance &amp; Benchmarking Dashboard
                </h2>
                <span className="text-[10px] font-bold uppercase tracking-wider bg-indigo-50 text-indigo-700 px-2 py-0.5 rounded-full border border-indigo-100">
                  Management Analytics
                </span>
              </div>
              <p className="text-xs text-slate-500 hidden sm:block">
                Sub-second latency verification, component breakdown, supplier reliability, and load testing
              </p>
            </div>
          </div>

          {/* Time Filter & Action Controls */}
          <div className="flex flex-wrap items-center gap-2 text-xs w-full lg:w-auto justify-end">
            {/* Time Range Pills */}
            <div className="flex items-center bg-slate-100 p-0.5 rounded-lg border border-slate-200">
              {[
                { id: "24h", label: "24h" },
                { id: "7d", label: "7d" },
                { id: "30d", label: "30d" },
                { id: "all", label: "All Time" },
              ].map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => setTimeRange(t.id)}
                  className={`px-2.5 py-1 rounded-md font-semibold transition-all cursor-pointer ${
                    timeRange === t.id
                      ? "bg-white text-slate-900 shadow-2xs"
                      : "text-slate-500 hover:text-slate-900"
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>

            {/* Include Benchmark Traffic Toggle */}
            <label className="flex items-center gap-1.5 px-2.5 py-1 bg-white border border-slate-200 rounded-lg text-slate-600 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={includeBenchmark}
                onChange={(e) => setIncludeBenchmark(e.target.checked)}
                className="w-3.5 h-3.5 rounded text-blue-600 focus:ring-blue-500"
              />
              <span className="text-[11px] font-medium">Include Benchmarks</span>
            </label>

            {/* Auto Refresh Toggle */}
            <button
              type="button"
              onClick={() => setAutoRefresh(!autoRefresh)}
              className={`px-2.5 py-1 rounded-lg border text-[11px] font-medium transition-colors cursor-pointer ${
                autoRefresh
                  ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                  : "bg-white text-slate-600 border-slate-200 hover:bg-slate-50"
              }`}
            >
              {autoRefresh ? "Auto (10s): ON" : "Auto: OFF"}
            </button>

            {/* Manual Refresh Button */}
            <button
              type="button"
              onClick={loadAllMetrics}
              disabled={loading || clearing}
              className="flex items-center gap-1.5 px-3 py-1 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-semibold transition-colors shadow-2xs cursor-pointer disabled:opacity-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
              <span className="hidden sm:inline">Refresh</span>
            </button>

            {/* Clear Dashboard Data Button */}
            <button
              type="button"
              onClick={handleClearMetrics}
              disabled={loading || clearing}
              title="Clear all performance records and reset dashboard"
              className="flex items-center gap-1.5 px-3 py-1 bg-rose-50 text-rose-700 hover:bg-rose-100 border border-rose-200 rounded-lg font-semibold transition-colors cursor-pointer disabled:opacity-50"
            >
              <Trash2 className={`w-3.5 h-3.5 ${clearing ? "animate-spin" : ""}`} />
              <span className="hidden sm:inline">{clearing ? "Clearing..." : "Clear Data"}</span>
            </button>
          </div>
        </div>

        {/* Analytics Sub-Tabs */}
        <div className="flex items-center gap-1 mt-3 border-t border-slate-100 pt-2.5 max-w-7xl mx-auto overflow-x-auto text-xs">
          {tabs.map((t) => {
            const Icon = t.icon
            const isActive = activeTab === t.id
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => setActiveTab(t.id)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg font-semibold transition-all whitespace-nowrap cursor-pointer ${
                  isActive
                    ? "bg-slate-900 text-white shadow-2xs"
                    : "text-slate-600 hover:bg-slate-100"
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                <span>{t.label}</span>
              </button>
            )
          })}
        </div>
      </div>

      {/* Main Analytics Content Area (Independently Scrollable) */}
      <div className="flex-1 min-h-0 overflow-y-auto p-4 sm:p-6">
        <div className="max-w-7xl mx-auto space-y-6">
          {errorMsg && (
            <div className="bg-rose-50 border border-rose-200 text-rose-800 px-4 py-3 rounded-xl flex items-center justify-between text-xs">
              <div className="flex items-center gap-2">
                <AlertTriangle className="w-4 h-4 text-rose-600 shrink-0" />
                <span>{errorMsg}</span>
              </div>
              <button
                type="button"
                onClick={loadAllMetrics}
                className="px-2.5 py-1 bg-rose-600 text-white rounded-md font-semibold hover:bg-rose-700 cursor-pointer"
              >
                Retry
              </button>
            </div>
          )}

          {/* Always Display Key KPI Cards */}
          <OverviewCards data={overviewData} loading={loading} />

          {/* If no searches recorded at all and not loading */}
          {!loading && !errorMsg && (overviewData?.total_searches ?? 0) === 0 && (
            <div className="bg-amber-50/70 border border-amber-200/80 rounded-xl p-4 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 text-xs text-amber-900">
              <div className="flex items-center gap-2.5">
                <AlertTriangle className="w-4 h-4 text-amber-600 shrink-0" />
                <div>
                  <span className="font-semibold">No metrics recorded yet for this time filter.</span>{" "}
                  Switch to "All Time", check "Include Benchmarks", or run an automated test in the Load Test Runner tab.
                </div>
              </div>
              <button
                type="button"
                onClick={() => setActiveTab("benchmark")}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-amber-600 text-white rounded-lg font-semibold hover:bg-amber-700 transition-colors shrink-0 cursor-pointer"
              >
                <PlayCircle className="w-3.5 h-3.5" />
                <span>Open Benchmark Runner</span>
              </button>
            </div>
          )}

          {/* Active Tab Panel */}
          {activeTab === "overview" && (
            <LatencyCharts
              latencyData={latencyData}
              historyData={historyData}
              loading={loading}
            />
          )}

          {activeTab === "suppliers" && (
            <SupplierComparison
              supplierData={supplierData}
              loading={loading}
            />
          )}

          {activeTab === "quality" && (
            <SearchQuality
              qualityData={qualityData}
              loading={loading}
            />
          )}

          {activeTab === "requests" && (
            <RequestHistory
              requests={requestsData.requests}
              total={requestsData.total}
              loading={loading}
              onSearch={setRequestSearchQuery}
              onStatusFilter={setRequestStatusFilter}
              statusFilter={requestStatusFilter}
            />
          )}

          {activeTab === "benchmark" && (
            <BenchmarkPanel
              onBenchmarkCompleted={loadAllMetrics}
            />
          )}
        </div>
      </div>
    </div>
  )
}
