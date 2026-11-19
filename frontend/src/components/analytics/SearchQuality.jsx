import React from "react"
import { ShieldCheck, CheckCircle2, AlertCircle, Sparkles, Filter, Layers } from "lucide-react"

export default function SearchQuality({ qualityData, loading }) {
  const discovery = qualityData?.product_discovery || {}
  const completeness = qualityData?.extraction_completeness || {}
  const verification = qualityData?.verification_coverage || {}
  const fields = completeness.fields || {}

  const fieldLabels = [
    { key: "title", label: "Product Title", desc: "Explicit part designation" },
    { key: "price", label: "Pricing & Currency", desc: "Numeric price parsed" },
    { key: "part_number", label: "Part Number / OEM", desc: "Manufacturer identifier" },
    { key: "brand", label: "Brand / Maker", desc: "Catalog manufacturer" },
    { key: "vehicle_compatibility", label: "Vehicle Compatibility", desc: "Variant fitment mapping" },
    { key: "stock_availability", label: "Stock Availability", desc: "Inventory presence status" },
    { key: "product_url", label: "Product URL", desc: "Direct canonical link" },
  ]

  return (
    <div className="space-y-4">
      {/* Top Row: Product Discovery & Relevance Filtering Metrics */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
        <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-xs">
          <div className="flex items-center justify-between mb-1">
            <span className="text-xs font-medium text-slate-500 uppercase tracking-wider">Total Discovered</span>
            <Sparkles className="w-4 h-4 text-purple-600" />
          </div>
          <div className="text-2xl font-bold text-slate-900 tracking-tight">
            {discovery.total_products_discovered ?? 0}
          </div>
          <div className="text-[11px] font-medium text-slate-500 mt-1">
            Found across all active suppliers
          </div>
        </div>

        <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-xs">
          <div className="flex items-center justify-between mb-1">
            <span className="text-xs font-medium text-slate-500 uppercase tracking-wider">Relevant Products</span>
            <CheckCircle2 className="w-4 h-4 text-emerald-600" />
          </div>
          <div className="text-2xl font-bold text-emerald-900 tracking-tight">
            {discovery.relevant_products_discovered ?? 0}
          </div>
          <div className="text-[11px] font-medium text-emerald-600 mt-1">
            {discovery.relevant_product_discovery_rate ?? 0}% relevant discovery rate
          </div>
        </div>

        <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-xs">
          <div className="flex items-center justify-between mb-1">
            <span className="text-xs font-medium text-slate-500 uppercase tracking-wider">Irrelevant Filtered</span>
            <Filter className="w-4 h-4 text-amber-600" />
          </div>
          <div className="text-2xl font-bold text-amber-900 tracking-tight">
            {discovery.irrelevant_products_filtered ?? 0}
          </div>
          <div className="text-[11px] font-medium text-amber-600 mt-1">
            Suppressed to protect mechanic trust
          </div>
        </div>

        <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-xs">
          <div className="flex items-center justify-between mb-1">
            <span className="text-xs font-medium text-slate-500 uppercase tracking-wider">Zero-Result Searches</span>
            <AlertCircle className="w-4 h-4 text-slate-400" />
          </div>
          <div className="text-2xl font-bold text-slate-900 tracking-tight">
            {discovery.zero_result_searches ?? 0}
          </div>
          <div className="text-[11px] font-medium text-slate-500 mt-1">
            Avg {discovery.avg_relevant_products_per_search ?? 0} parts/search
          </div>
        </div>
      </div>

      {/* Discovery Rate Definition Note */}
      <div className="bg-blue-50/60 border border-blue-200/70 rounded-xl p-3.5 flex items-start gap-3 text-xs text-blue-900">
        <Sparkles className="w-4 h-4 text-blue-600 shrink-0 mt-0.5" />
        <div>
          <span className="font-semibold">Product Discovery Rate Definition:</span>{" "}
          Distinguishes between raw product availability (
          <strong>{discovery.any_product_discovery_rate ?? 0}%</strong> of searches found &ge;1 listing) and semantic relevance (
          <strong>{discovery.relevant_product_discovery_rate ?? 0}%</strong> of searches found &ge;1 verified compatible part).
        </div>
      </div>

      {/* Two Column Section: Extraction Completeness & Verification Coverage */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
        {/* Extraction Completeness Breakdown (7 cols) */}
        <div className="lg:col-span-7 bg-white border border-slate-200 rounded-xl p-4 shadow-xs">
          <div className="flex items-center justify-between pb-3 border-b border-slate-100 mb-3">
            <div className="flex items-center gap-2">
              <Layers className="w-4 h-4 text-indigo-600" />
              <h3 className="font-semibold text-slate-900 text-sm">Data Extraction Completeness</h3>
            </div>
            <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-700 border border-indigo-100">
              {completeness.overall_completeness_rate ?? 0}% Overall Completeness
            </span>
          </div>

          <div className="space-y-3">
            {fieldLabels.map((f, idx) => {
              const pct = fields[f.key] ?? 0.0
              return (
                <div key={idx} className="space-y-1">
                  <div className="flex items-center justify-between text-xs">
                    <div>
                      <span className="font-semibold text-slate-800">{f.label}</span>
                      <span className="text-slate-400 text-[11px] ml-1.5 hidden sm:inline">({f.desc})</span>
                    </div>
                    <span className="font-bold text-slate-900">{pct}%</span>
                  </div>
                  <div className="bg-slate-100 h-2 rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-500 ${
                        pct >= 90 ? "bg-emerald-500" : pct >= 70 ? "bg-indigo-500" : "bg-amber-500"
                      }`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </div>
              )
            })}
          </div>
        </div>

        {/* Verification Coverage & Ground-Truth Explanation (5 cols) */}
        <div className="lg:col-span-5 bg-white border border-slate-200 rounded-xl p-4 shadow-xs flex flex-col justify-between space-y-4">
          <div>
            <div className="flex items-center justify-between pb-3 border-b border-slate-100 mb-3">
              <div className="flex items-center gap-2">
                <ShieldCheck className="w-4 h-4 text-emerald-600" />
                <h3 className="font-semibold text-slate-900 text-sm">Verification Coverage</h3>
              </div>
              <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-100">
                {verification.overall_coverage_rate ?? 0}% Verified
              </span>
            </div>

            <div className="space-y-3 text-xs">
              <div className="bg-slate-50 border border-slate-200/80 rounded-lg p-3">
                <div className="flex justify-between font-semibold text-slate-800 mb-1">
                  <span>Fitment Compatibility Verified:</span>
                  <span className="text-emerald-700">{verification.fitment_verified_rate ?? 0}%</span>
                </div>
                <div className="text-[11px] text-slate-500">
                  {verification.verified_fitment_count ?? 0} listings checked against supplier vehicle fitment table
                </div>
              </div>

              <div className="bg-slate-50 border border-slate-200/80 rounded-lg p-3">
                <div className="flex justify-between font-semibold text-slate-800 mb-1">
                  <span>Seller Stock &amp; Dispatch Verified:</span>
                  <span className="text-emerald-700">{verification.stock_verified_rate ?? 0}%</span>
                </div>
                <div className="text-[11px] text-slate-500">
                  {verification.verified_stock_count ?? 0} listings confirmed available with dispatch time
                </div>
              </div>
            </div>
          </div>

          {/* Critical Disclaimer Card */}
          <div className="bg-slate-50 border border-slate-200 rounded-lg p-3 text-[11px] text-slate-600 space-y-1">
            <div className="font-semibold text-slate-800 flex items-center gap-1.5">
              <AlertCircle className="w-3.5 h-3.5 text-slate-500" />
              <span>Ground-Truth &amp; Accuracy Methodology</span>
            </div>
            <p className="text-slate-500 leading-relaxed">
              We strictly label these metrics as <strong>extraction completeness</strong> and{" "}
              <strong>verification coverage</strong>. 100% ground-truth accuracy requires an authoritative
              OEM manufacturer catalogue reference comparison.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
