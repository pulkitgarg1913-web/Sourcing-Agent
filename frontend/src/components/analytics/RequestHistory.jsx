import React, { useState } from "react"
import { Search, Eye, X, Clock, Layers, Server, CheckCircle2, AlertTriangle, AlertCircle, ExternalLink } from "lucide-react"
import { fetchRequestDetail } from "../../services/api"

export default function RequestHistory({
  requests = [],
  total = 0,
  loading = false,
  onSearch,
  onStatusFilter,
  statusFilter = "all",
}) {
  const [searchTerm, setSearchTerm] = useState("")
  const [selectedRequest, setSelectedRequest] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState(null)

  const handleSearchSubmit = (e) => {
    e.preventDefault()
    if (onSearch) onSearch(searchTerm)
  }

  const handleOpenDetail = async (reqId) => {
    setDetailLoading(true)
    setDetailError(null)
    setSelectedRequest(null)
    try {
      const data = await fetchRequestDetail(reqId)
      setSelectedRequest(data)
    } catch (err) {
      setDetailError(err.message || "Failed to load request details")
    } finally {
      setDetailLoading(false)
    }
  }

  return (
    <div className="space-y-4">
      {/* Search and Filter Controls */}
      <div className="bg-white border border-slate-200 rounded-xl p-3 sm:p-4 shadow-xs flex flex-col sm:flex-row gap-3 items-center justify-between">
        <form onSubmit={handleSearchSubmit} className="relative w-full sm:w-80">
          <Search className="w-4 h-4 text-slate-400 absolute left-3 top-2.5 pointer-events-none" />
          <input
            type="text"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="Search query, part, or vehicle..."
            className="w-full pl-9 pr-3 py-1.5 text-xs bg-slate-50 border border-slate-200 rounded-lg focus:outline-hidden focus:ring-2 focus:ring-blue-500 focus:bg-white transition-all text-slate-900"
          />
        </form>

        <div className="flex items-center gap-2 w-full sm:w-auto justify-end">
          <span className="text-xs text-slate-500 font-medium">Status:</span>
          <select
            value={statusFilter}
            onChange={(e) => onStatusFilter(e.target.value)}
            className="text-xs font-medium bg-slate-50 border border-slate-200 rounded-lg px-2.5 py-1.5 text-slate-700 focus:outline-hidden focus:ring-2 focus:ring-blue-500"
          >
            <option value="all">All Statuses</option>
            <option value="success">Success</option>
            <option value="completed_no_results">Zero Results</option>
            <option value="partial_success">Partial Success</option>
            <option value="failed">Failed</option>
            <option value="timeout">Timeout</option>
          </select>
          <span className="text-xs font-semibold text-slate-500 ml-2">
            {total} total
          </span>
        </div>
      </div>

      {/* Requests History Table */}
      <div className="bg-white border border-slate-200 rounded-xl overflow-hidden shadow-xs">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs border-collapse">
            <thead>
              <tr className="bg-slate-50 text-slate-500 font-semibold border-b border-slate-200 uppercase tracking-wider text-[10px]">
                <th className="py-3 px-4">Request ID</th>
                <th className="py-3 px-4">Customer Query</th>
                <th className="py-3 px-4">Timestamp</th>
                <th className="py-3 px-4 text-right">Total Latency</th>
                <th className="py-3 px-4 text-center">Suppliers</th>
                <th className="py-3 px-4 text-right">Products</th>
                <th className="py-3 px-4 text-center">Status</th>
                <th className="py-3 px-4 text-right">Inspect</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 text-slate-700">
              {requests.length === 0 ? (
                <tr>
                  <td colSpan="8" className="py-10 text-center text-slate-400">
                    {loading ? "Loading requests..." : "No search requests match the criteria."}
                  </td>
                </tr>
              ) : (
                requests.map((r, idx) => (
                  <tr key={idx} className="hover:bg-slate-50/70 transition-colors">
                    <td className="py-3 px-4 font-mono font-medium text-slate-500 text-[11px] truncate max-w-[120px]" title={r.request_id}>
                      {r.request_id}
                    </td>
                    <td className="py-3 px-4 font-semibold text-slate-900 max-w-[240px] truncate" title={r.customer_query}>
                      {r.customer_query}
                      {r.vehicle_label && (
                        <div className="text-[10px] font-normal text-slate-500 truncate">{r.vehicle_label}</div>
                      )}
                    </td>
                    <td className="py-3 px-4 text-slate-500 whitespace-nowrap">
                      {new Date(r.timestamp * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                    </td>
                    <td className="py-3 px-4 text-right font-bold text-slate-900 whitespace-nowrap">
                      {r.total_latency_seconds}s
                      {r.is_cached && (
                        <span className="ml-1 text-[9px] px-1 py-0.2 bg-emerald-50 text-emerald-700 border border-emerald-200 rounded font-normal">
                          cached
                        </span>
                      )}
                    </td>
                    <td className="py-3 px-4 text-center font-medium">
                      <span className="text-slate-600">{r.suppliers_succeeded}/{r.suppliers_count || 2}</span>
                    </td>
                    <td className="py-3 px-4 text-right font-semibold text-slate-800">
                      {r.products_discovered}
                      {r.relevant_products !== undefined && (
                        <span className="text-slate-400 text-[10px] font-normal"> ({r.relevant_products} rel)</span>
                      )}
                    </td>
                    <td className="py-3 px-4 text-center">
                      <span
                        className={`px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wider ${
                          r.status === "success" || r.status === "completed"
                            ? "bg-emerald-50 text-emerald-700 border border-emerald-100"
                            : r.status === "completed_no_results"
                            ? "bg-slate-100 text-slate-600 border border-slate-200"
                            : r.status === "partial_success"
                            ? "bg-blue-50 text-blue-700 border border-blue-100"
                            : r.status === "timeout"
                            ? "bg-amber-50 text-amber-700 border border-amber-100"
                            : "bg-rose-50 text-rose-700 border border-rose-100"
                        }`}
                      >
                        {r.status.replace("_", " ")}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-right">
                      <button
                        type="button"
                        onClick={() => handleOpenDetail(r.request_id)}
                        className="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium rounded bg-slate-100 text-slate-700 hover:bg-blue-50 hover:text-blue-700 transition-colors cursor-pointer"
                      >
                        <Eye className="w-3.5 h-3.5" />
                        <span>Inspect</span>
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Request Inspection Modal */}
      {(selectedRequest || detailLoading || detailError) && (
        <div className="fixed inset-0 z-50 bg-slate-900/40 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-xl border border-slate-200 w-full max-w-3xl max-h-[85vh] flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-150">
            {/* Modal Header */}
            <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between shrink-0 bg-slate-50/50">
              <div>
                <h3 className="font-bold text-slate-900 text-base">Request Performance Inspection</h3>
                <p className="text-xs text-slate-500 font-mono mt-0.5">
                  ID: {selectedRequest?.request_id || "Loading..."}
                </p>
              </div>
              <button
                type="button"
                onClick={() => {
                  setSelectedRequest(null)
                  setDetailError(null)
                }}
                className="p-1 rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors cursor-pointer"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Modal Content */}
            <div className="p-5 overflow-y-auto space-y-4 flex-1">
              {detailLoading ? (
                <div className="py-12 text-center text-sm text-slate-500">
                  Loading request execution records and stage timings...
                </div>
              ) : detailError ? (
                <div className="p-4 bg-rose-50 text-rose-700 text-sm rounded-lg border border-rose-200">
                  {detailError}
                </div>
              ) : selectedRequest ? (
                <>
                  {/* Top Summary Info */}
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 bg-slate-50 p-3 rounded-xl border border-slate-200/80 text-xs">
                    <div>
                      <span className="text-slate-400 block text-[10px] uppercase font-semibold">Query</span>
                      <span className="font-semibold text-slate-900">{selectedRequest.customer_query}</span>
                    </div>
                    <div>
                      <span className="text-slate-400 block text-[10px] uppercase font-semibold">Total Latency</span>
                      <span className="font-bold text-slate-900">{selectedRequest.total_latency_seconds}s ({selectedRequest.total_latency_ms}ms)</span>
                    </div>
                    <div>
                      <span className="text-slate-400 block text-[10px] uppercase font-semibold">Status</span>
                      <span className="font-bold text-emerald-600 uppercase">{selectedRequest.status}</span>
                    </div>
                    <div>
                      <span className="text-slate-400 block text-[10px] uppercase font-semibold">Products</span>
                      <span className="font-bold text-slate-900">{selectedRequest.total_products_discovered} found</span>
                    </div>
                  </div>

                  {/* 10-Stage Component Timings */}
                  <div className="space-y-2">
                    <div className="flex items-center gap-1.5 font-semibold text-slate-900 text-xs">
                      <Clock className="w-4 h-4 text-blue-600" />
                      <span>Stage-Level Timings Breakdown</span>
                    </div>
                    <div className="border border-slate-200 rounded-xl overflow-hidden">
                      <table className="w-full text-xs text-left">
                        <thead className="bg-slate-50 text-slate-500 text-[10px] font-semibold uppercase border-b border-slate-200">
                          <tr>
                            <th className="py-2 px-3">Stage</th>
                            <th className="py-2 px-3 text-right">Duration (ms)</th>
                            <th className="py-2 px-3 text-right">Duration (s)</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100 text-slate-700">
                          {selectedRequest.stages?.map((stg, i) => (
                            <tr key={i} className="hover:bg-slate-50/50">
                              <td className="py-1.5 px-3 font-medium text-slate-800">
                                {stg.stage.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
                              </td>
                              <td className="py-1.5 px-3 text-right font-mono">{stg.duration_ms} ms</td>
                              <td className="py-1.5 px-3 text-right font-bold text-slate-900">{stg.duration_seconds} s</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>

                  {/* Supplier Execution Records */}
                  <div className="space-y-2">
                    <div className="flex items-center gap-1.5 font-semibold text-slate-900 text-xs">
                      <Server className="w-4 h-4 text-indigo-600" />
                      <span>Supplier Execution Details</span>
                    </div>
                    <div className="border border-slate-200 rounded-xl overflow-hidden">
                      <table className="w-full text-xs text-left">
                        <thead className="bg-slate-50 text-slate-500 text-[10px] font-semibold uppercase border-b border-slate-200">
                          <tr>
                            <th className="py-2 px-3">Supplier</th>
                            <th className="py-2 px-3 text-center">Status</th>
                            <th className="py-2 px-3 text-right">Latency</th>
                            <th className="py-2 px-3 text-right">Products</th>
                            <th className="py-2 px-3 text-right">Navigation</th>
                            <th className="py-2 px-3 text-right">Extraction</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100 text-slate-700">
                          {selectedRequest.suppliers?.map((sup, i) => (
                            <tr key={i} className="hover:bg-slate-50/50">
                              <td className="py-2 px-3 font-semibold text-slate-900">{sup.supplier_name}</td>
                              <td className="py-2 px-3 text-center">
                                <span className="font-semibold text-emerald-600">{sup.status}</span>
                              </td>
                              <td className="py-2 px-3 text-right font-bold text-slate-900">{sup.latency_seconds}s</td>
                              <td className="py-2 px-3 text-right font-medium">{sup.products_discovered}</td>
                              <td className="py-2 px-3 text-right text-slate-500">{sup.navigation_seconds}s</td>
                              <td className="py-2 px-3 text-right text-slate-500">{sup.extraction_seconds}s</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </>
              ) : null}
            </div>

            {/* Modal Footer */}
            <div className="px-5 py-3 border-t border-slate-100 flex justify-end shrink-0 bg-slate-50">
              <button
                type="button"
                onClick={() => setSelectedRequest(null)}
                className="px-4 py-1.5 text-xs font-semibold text-slate-700 bg-white border border-slate-200 rounded-lg hover:bg-slate-100 transition-colors cursor-pointer"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
