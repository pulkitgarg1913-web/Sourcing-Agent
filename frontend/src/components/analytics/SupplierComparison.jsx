import React from "react"
import { Server, CheckCircle2, AlertTriangle, Clock, Package, ShieldCheck } from "lucide-react"

export default function SupplierComparison({ supplierData, loading }) {
  const suppliers = supplierData?.suppliers || []

  return (
    <div className="space-y-4">
      {/* Supplier Performance Comparison Table */}
      <div className="bg-white border border-slate-200 rounded-xl overflow-hidden shadow-xs">
        <div className="p-4 border-b border-slate-100 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Server className="w-4 h-4 text-blue-600" />
            <h3 className="font-semibold text-slate-900 text-sm">Supplier Performance Comparison</h3>
          </div>
          <span className="text-xs text-slate-500">
            {suppliers.length} Configured Supplier{suppliers.length !== 1 ? "s" : ""}
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs border-collapse">
            <thead>
              <tr className="bg-slate-50 text-slate-500 font-semibold border-b border-slate-200 uppercase tracking-wider text-[10px]">
                <th className="py-3 px-4">Supplier</th>
                <th className="py-3 px-4 text-right">Requests</th>
                <th className="py-3 px-4 text-right">Success Rate</th>
                <th className="py-3 px-4 text-right">Avg Latency</th>
                <th className="py-3 px-4 text-right">P95 Latency</th>
                <th className="py-3 px-4 text-right">Products Found</th>
                <th className="py-3 px-4 text-right">Availability</th>
                <th className="py-3 px-4 text-center">Health Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 text-slate-700">
              {suppliers.length === 0 ? (
                <tr>
                  <td colSpan="8" className="py-8 text-center text-slate-400">
                    No supplier performance metrics recorded yet.
                  </td>
                </tr>
              ) : (
                suppliers.map((s, idx) => (
                  <tr key={idx} className="hover:bg-slate-50/70 transition-colors">
                    <td className="py-3 px-4 font-semibold text-slate-900 flex items-center gap-2">
                      <span className="w-2 h-2 rounded-full bg-blue-600" />
                      <span>{s.supplier_name}</span>
                    </td>
                    <td className="py-3 px-4 text-right font-medium">{s.requests}</td>
                    <td className="py-3 px-4 text-right">
                      <span
                        className={`font-semibold px-2 py-0.5 rounded-full text-[11px] ${
                          s.success_rate >= 90
                            ? "bg-emerald-50 text-emerald-700 border border-emerald-100"
                            : s.success_rate >= 70
                            ? "bg-amber-50 text-amber-700 border border-amber-100"
                            : "bg-rose-50 text-rose-700 border border-rose-100"
                        }`}
                      >
                        {s.success_rate}%
                      </span>
                    </td>
                    <td className="py-3 px-4 text-right font-semibold text-slate-900">
                      {s.avg_latency_seconds}s
                    </td>
                    <td className="py-3 px-4 text-right font-semibold text-amber-900">
                      {s.p95_latency_seconds}s
                    </td>
                    <td className="py-3 px-4 text-right font-medium">
                      {s.products_discovered}{" "}
                      <span className="text-slate-400 text-[10px]">({s.avg_products_per_search}/req)</span>
                    </td>
                    <td className="py-3 px-4 text-right font-medium text-slate-700">
                      {s.availability_rate}%
                    </td>
                    <td className="py-3 px-4 text-center">
                      {s.availability_rate >= 90 ? (
                        <span className="inline-flex items-center gap-1 text-[11px] font-medium text-emerald-600">
                          <CheckCircle2 className="w-3.5 h-3.5" />
                          <span>Optimal</span>
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-[11px] font-medium text-amber-600">
                          <AlertTriangle className="w-3.5 h-3.5" />
                          <span>Degraded</span>
                        </span>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Side-by-Side Detailed Supplier Breakdown Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {suppliers.map((s, idx) => (
          <div key={idx} className="bg-white border border-slate-200 rounded-xl p-4 shadow-xs space-y-3">
            <div className="flex items-center justify-between pb-2 border-b border-slate-100">
              <div className="flex items-center gap-2">
                <div className="w-3 h-3 rounded-full bg-blue-600" />
                <h4 className="font-semibold text-slate-900 text-sm">{s.supplier_name} Sourcing Analytics</h4>
              </div>
              <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-slate-100 text-slate-600">
                {s.requests} searches
              </span>
            </div>

            {/* Quick Stats Grid */}
            <div className="grid grid-cols-3 gap-2 text-center text-xs">
              <div className="bg-slate-50 border border-slate-200/70 p-2 rounded-lg">
                <div className="text-[10px] text-slate-500 font-medium">Success Rate</div>
                <div className="text-base font-bold text-slate-900 mt-0.5">{s.success_rate}%</div>
                <div className="text-[10px] text-slate-400">{s.failed_searches} failed</div>
              </div>
              <div className="bg-slate-50 border border-slate-200/70 p-2 rounded-lg">
                <div className="text-[10px] text-slate-500 font-medium">Avg Latency</div>
                <div className="text-base font-bold text-slate-900 mt-0.5">{s.avg_latency_seconds}s</div>
                <div className="text-[10px] text-slate-400">P95: {s.p95_latency_seconds}s</div>
              </div>
              <div className="bg-slate-50 border border-slate-200/70 p-2 rounded-lg">
                <div className="text-[10px] text-slate-500 font-medium">Parts Found</div>
                <div className="text-base font-bold text-slate-900 mt-0.5">{s.products_discovered}</div>
                <div className="text-[10px] text-slate-400">{s.products_extracted} extracted</div>
              </div>
            </div>

            {/* Supplier Stage Breakdown */}
            {s.stage_breakdown && (
              <div className="space-y-1.5 pt-2 border-t border-slate-100 text-xs">
                <div className="text-[11px] font-semibold text-slate-700">Internal Execution Stages (Avg Seconds):</div>
                <div className="flex justify-between text-slate-600">
                  <span>Browser Context Init:</span>
                  <span className="font-semibold text-slate-900">{s.stage_breakdown.browser_init_seconds}s</span>
                </div>
                <div className="flex justify-between text-slate-600">
                  <span>Navigation &amp; Rendering:</span>
                  <span className="font-semibold text-slate-900">{s.stage_breakdown.navigation_seconds}s</span>
                </div>
                <div className="flex justify-between text-slate-600">
                  <span>Product Listing Extraction:</span>
                  <span className="font-semibold text-slate-900">{s.stage_breakdown.extraction_seconds}s</span>
                </div>
                <div className="flex justify-between text-slate-600">
                  <span>Detail &amp; Offer Verification:</span>
                  <span className="font-semibold text-slate-900">{s.stage_breakdown.verification_seconds}s</span>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
