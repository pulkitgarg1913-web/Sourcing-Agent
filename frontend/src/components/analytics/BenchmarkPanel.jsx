import React, { useState, useEffect } from "react"
import { Play, StopCircle, RefreshCw, Zap, Gauge, Clock, CheckCircle2, AlertTriangle, Layers, Server } from "lucide-react"
import { runBenchmark, fetchBenchmarkStatus, cancelBenchmark } from "../../services/api"

export default function BenchmarkPanel({ onBenchmarkCompleted }) {
  const [concurrency, setConcurrency] = useState(1)
  const [mode, setMode] = useState("sequential")
  const [queryCount, setQueryCount] = useState(7)
  const [isRunning, setIsRunning] = useState(false)
  const [latestSummary, setLatestSummary] = useState(null)
  const [error, setError] = useState(null)

  const standardQueries = [
    "Hyundai Creta Headlight",
    "Kia Seltos Front Bumper",
    "Tata Nexon Gearbox",
    "Maruti Baleno Air Filter",
    "Toyota Fortuner Side Mirror",
    "Honda City Brake Pads",
    "Tata Harrier Oil Filter",
  ]

  // Poll status on mount or when running
  useEffect(() => {
    let interval = null
    const checkStatus = async () => {
      try {
        const res = await fetchBenchmarkStatus()
        setIsRunning(res.is_running)
        if (res.latest_result) {
          setLatestSummary(res.latest_result)
        }
      } catch (e) {
        // silent
      }
    }
    checkStatus()

    if (isRunning) {
      interval = setInterval(checkStatus, 2000)
    }
    return () => {
      if (interval) clearInterval(interval)
    }
  }, [isRunning])

  const handleStartBenchmark = async () => {
    setError(null)
    setIsRunning(true)
    try {
      const selectedQueries = standardQueries.slice(0, queryCount)
      const res = await runBenchmark({
        concurrency,
        mode,
        queries: selectedQueries,
      })
      if (res && res.summary) {
        setLatestSummary(res.summary)
        if (onBenchmarkCompleted) onBenchmarkCompleted()
      }
    } catch (err) {
      setError(err.message || "Benchmark failed to run")
    } finally {
      setIsRunning(false)
    }
  }

  const handleCancelBenchmark = async () => {
    try {
      await cancelBenchmark()
      setIsRunning(false)
    } catch (e) {
      // silent
    }
  }

  return (
    <div className="space-y-4">
      {/* Benchmark Control Bar */}
      <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-xs space-y-4">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 pb-3 border-b border-slate-100">
          <div>
            <div className="flex items-center gap-2">
              <Zap className="w-5 h-5 text-amber-500" />
              <h3 className="font-bold text-slate-900 text-sm">Automated Agent Benchmark &amp; Load Testing</h3>
            </div>
            <p className="text-xs text-slate-500 mt-0.5">
              Evaluates the agent under controlled workloads using realistic automotive queries with high-resolution monotonic timers.
            </p>
          </div>
          <div className="flex items-center gap-2">
            {isRunning ? (
              <button
                type="button"
                onClick={handleCancelBenchmark}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-rose-700 bg-rose-50 border border-rose-200 rounded-lg hover:bg-rose-100 transition-colors cursor-pointer"
              >
                <StopCircle className="w-4 h-4 text-rose-600 animate-pulse" />
                <span>Cancel Benchmark</span>
              </button>
            ) : (
              <button
                type="button"
                onClick={handleStartBenchmark}
                className="flex items-center gap-1.5 px-4 py-1.5 text-xs font-semibold text-white bg-blue-600 rounded-lg hover:bg-blue-700 transition-colors shadow-xs cursor-pointer"
              >
                <Play className="w-3.5 h-3.5 fill-current" />
                <span>Run Benchmark Now</span>
              </button>
            )}
          </div>
        </div>

        {/* Configuration Options */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs">
          {/* Concurrency Selector */}
          <div className="space-y-1.5">
            <label className="font-semibold text-slate-700 block">Concurrency Level:</label>
            <div className="flex items-center gap-1">
              {[1, 5, 10, 20].map((lvl) => (
                <button
                  key={lvl}
                  type="button"
                  disabled={isRunning}
                  onClick={() => {
                    setConcurrency(lvl)
                    setMode(lvl === 1 ? "sequential" : "concurrent")
                  }}
                  className={`flex-1 py-1.5 text-center font-bold rounded-lg border transition-all cursor-pointer ${
                    concurrency === lvl
                      ? "bg-blue-600 text-white border-blue-600 shadow-xs"
                      : "bg-slate-50 text-slate-700 border-slate-200 hover:bg-slate-100"
                  }`}
                >
                  {lvl}x
                </button>
              ))}
            </div>
            <span className="text-[10px] text-slate-400">
              {concurrency === 1 ? "Sequential execution" : `${concurrency} concurrent requests via semaphore`}
            </span>
          </div>

          {/* Mode Selector */}
          <div className="space-y-1.5">
            <label className="font-semibold text-slate-700 block">Execution Mode:</label>
            <div className="flex items-center gap-1">
              <button
                type="button"
                disabled={isRunning}
                onClick={() => {
                  setMode("sequential")
                  setConcurrency(1)
                }}
                className={`flex-1 py-1.5 text-center font-bold rounded-lg border transition-all cursor-pointer ${
                  mode === "sequential"
                    ? "bg-blue-600 text-white border-blue-600 shadow-xs"
                    : "bg-slate-50 text-slate-700 border-slate-200 hover:bg-slate-100"
                }`}
              >
                Sequential
              </button>
              <button
                type="button"
                disabled={isRunning}
                onClick={() => {
                  setMode("concurrent")
                  if (concurrency === 1) setConcurrency(5)
                }}
                className={`flex-1 py-1.5 text-center font-bold rounded-lg border transition-all cursor-pointer ${
                  mode === "concurrent"
                    ? "bg-blue-600 text-white border-blue-600 shadow-xs"
                    : "bg-slate-50 text-slate-700 border-slate-200 hover:bg-slate-100"
                }`}
              >
                Concurrent
              </button>
            </div>
            <span className="text-[10px] text-slate-400">
              Tests queue handling and progressive results
            </span>
          </div>

          {/* Test Queries Count */}
          <div className="space-y-1.5">
            <label className="font-semibold text-slate-700 block">Test Queries ({queryCount} selected):</label>
            <select
              value={queryCount}
              disabled={isRunning}
              onChange={(e) => setQueryCount(Number(e.target.value))}
              className="w-full bg-slate-50 border border-slate-200 rounded-lg px-2.5 py-1.5 font-medium text-slate-700"
            >
              <option value={2}>Fast Check (2 queries)</option>
              <option value={4}>Standard Pack (4 queries)</option>
              <option value={7}>Full Automotive Suite (7 queries)</option>
            </select>
            <span className="text-[10px] text-slate-400">
              Realistic queries (Creta, Seltos, Nexon, Baleno, etc.)
            </span>
          </div>
        </div>

        {error && (
          <div className="p-3 bg-rose-50 text-rose-700 text-xs rounded-lg border border-rose-200">
            {error}
          </div>
        )}

        {isRunning && (
          <div className="p-3 bg-blue-50 border border-blue-200 rounded-lg flex items-center gap-3 text-xs text-blue-900">
            <RefreshCw className="w-4 h-4 text-blue-600 animate-spin shrink-0" />
            <div>
              <span className="font-semibold">Executing Benchmark...</span> Measuring live end-to-end response times under{" "}
              <strong>{concurrency}x concurrency</strong>.
            </div>
          </div>
        )}
      </div>

      {/* Latest Benchmark Results Summary */}
      {latestSummary && (
        <div className="space-y-4">
          {/* KPI Summary Cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="bg-white border border-slate-200 rounded-xl p-3.5 shadow-xs">
              <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Throughput</span>
              <div className="text-2xl font-bold text-slate-900 mt-1">
                {latestSummary.throughput_rps} <span className="text-xs font-normal text-slate-500">req/s</span>
              </div>
              <div className="text-[11px] text-slate-500 mt-0.5">
                {latestSummary.total_duration_seconds}s total duration
              </div>
            </div>

            <div className="bg-white border border-slate-200 rounded-xl p-3.5 shadow-xs">
              <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">P50 Latency</span>
              <div className="text-2xl font-bold text-slate-900 mt-1">
                {latestSummary.latency?.p50_seconds}s
              </div>
              <div className="text-[11px] text-slate-500 mt-0.5">
                Avg: {latestSummary.latency?.avg_seconds}s
              </div>
            </div>

            <div className="bg-white border border-slate-200 rounded-xl p-3.5 shadow-xs">
              <span className="text-[10px] font-bold text-amber-700 uppercase tracking-wider block">P95 / P99 Latency</span>
              <div className="text-2xl font-bold text-amber-900 mt-1">
                {latestSummary.latency?.p95_seconds}s <span className="text-xs font-normal text-slate-500">/ {latestSummary.latency?.p99_seconds}s</span>
              </div>
              <div className="text-[11px] text-amber-600 mt-0.5">
                Max peak: {latestSummary.latency?.max_seconds}s
              </div>
            </div>

            <div className="bg-white border border-slate-200 rounded-xl p-3.5 shadow-xs">
              <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Success &amp; Discovery</span>
              <div className="text-2xl font-bold text-emerald-900 mt-1">
                {latestSummary.product_discovery_rate}%
              </div>
              <div className="text-[11px] text-emerald-600 mt-0.5">
                {latestSummary.successful_queries}/{latestSummary.total_queries} succeeded ({latestSummary.error_rate}% error)
              </div>
            </div>
          </div>

          {/* Detailed Per-Query Results Table */}
          <div className="bg-white border border-slate-200 rounded-xl overflow-hidden shadow-xs">
            <div className="p-3 border-b border-slate-100 flex items-center justify-between">
              <span className="font-semibold text-slate-900 text-xs">Evaluated Benchmark Queries</span>
              <span className="text-[11px] text-slate-400 font-mono">
                Tag: is_benchmark = 1
              </span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs border-collapse">
                <thead>
                  <tr className="bg-slate-50 text-slate-500 text-[10px] font-semibold uppercase border-b border-slate-200">
                    <th className="py-2.5 px-3">Test Query</th>
                    <th className="py-2.5 px-3 text-right">Monotonic Latency</th>
                    <th className="py-2.5 px-3 text-right">Products Found</th>
                    <th className="py-2.5 px-3 text-right">Relevant Parts</th>
                    <th className="py-2.5 px-3 text-center">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 text-slate-700">
                  {latestSummary.queries_evaluated?.map((q, i) => (
                    <tr key={i} className="hover:bg-slate-50/50">
                      <td className="py-2 px-3 font-semibold text-slate-900">{q.query}</td>
                      <td className="py-2 px-3 text-right font-bold text-slate-900">{q.latency_seconds}s</td>
                      <td className="py-2 px-3 text-right font-medium">{q.products_found}</td>
                      <td className="py-2 px-3 text-right text-emerald-600 font-semibold">{q.relevant_products}</td>
                      <td className="py-2 px-3 text-center">
                        <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase bg-emerald-50 text-emerald-700 border border-emerald-100">
                          {q.status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
