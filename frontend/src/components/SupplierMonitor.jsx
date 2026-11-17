import React from "react"
import {
  Clock,
  Package,
  CheckCircle2,
  AlertCircle,
  Loader2,
  ArrowRight,
} from "lucide-react"

export default function SupplierMonitor({
  suppliers,
  onViewSupplierResults,
  activeFilter,
}) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-xs p-4">
      {/* Section Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-emerald-500" />
          <h2 className="text-xs font-semibold text-slate-800 uppercase tracking-wider">
            Supplier Search Status
          </h2>
        </div>
        <span className="text-[11px] text-slate-500 font-medium">
          Concurrent Discovery
        </span>
      </div>

      {/* Supplier Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {suppliers.map((s) => {
          const isCompleted = s.status === "Completed" || s.status === "No Results"
          const isFailed = s.status === "Failed"
          const isBlocked = s.status === "Blocked"
          const isInProgress =
            !isCompleted &&
            !isFailed &&
            !isBlocked &&
            s.status !== "Waiting" &&
            s.status !== "Pending" &&
            s.status !== "Idle"

          const statusColorMap = {
            Waiting: "bg-slate-50 text-slate-600 border-slate-200",
            Pending: "bg-slate-50 text-slate-600 border-slate-200",
            Searching: "bg-blue-50 text-blue-700 border-blue-200",
            "Initializing browser": "bg-blue-50 text-blue-700 border-blue-200",
            "Opening supplier website": "bg-blue-50 text-blue-700 border-blue-200",
            "Selecting vehicle": "bg-blue-50 text-blue-700 border-blue-200",
            "Navigating to category": "bg-blue-50 text-blue-700 border-blue-200",
            "Searching products": "bg-indigo-50 text-indigo-700 border-indigo-200",
            "Extracting listings": "bg-purple-50 text-purple-700 border-purple-200",
            Completed: "bg-emerald-50 text-emerald-700 border-emerald-200",
            "No Results": "bg-slate-100 text-slate-600 border-slate-200",
            Failed: "bg-rose-50 text-rose-700 border-rose-200",
            Blocked: "bg-amber-50 text-amber-700 border-amber-200",
          }

          const badgeClass =
            statusColorMap[s.status] ||
            "bg-blue-50 text-blue-700 border-blue-200"

          return (
            <div
              key={s.id}
              className={`rounded-lg border p-3 flex flex-col justify-between transition-all ${
                activeFilter === s.vendor
                  ? "border-blue-500 bg-blue-50/20 shadow-xs"
                  : "border-slate-200 bg-white"
              }`}
            >
              <div>
                {/* Supplier Top Row */}
                <div className="flex items-center justify-between gap-2 mb-2">
                  <div className="flex items-center gap-2">
                    <div
                      className={`w-7 h-7 rounded-md flex items-center justify-center font-bold text-xs ${
                        s.vendor === "boodmo"
                          ? "bg-red-50 text-red-600 border border-red-200"
                          : "bg-orange-50 text-orange-600 border border-orange-200"
                      }`}
                    >
                      {s.name[0]}
                    </div>
                    <div>
                      <h3 className="text-xs font-semibold text-slate-900 leading-none">
                        {s.name}
                      </h3>
                      <span className="text-[10px] text-slate-400">
                        {s.vendor === "boodmo" ? "Marketplace" : "Distributor"}
                      </span>
                    </div>
                  </div>

                  {/* Status Badge */}
                  <span
                    className={`inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full border ${badgeClass}`}
                  >
                    {isInProgress && (
                      <Loader2 className="w-2.5 h-2.5 animate-spin" />
                    )}
                    {isCompleted && (
                      s.status === "No Results" ? (
                        <Package className="w-2.5 h-2.5 text-slate-400" />
                      ) : (
                        <CheckCircle2 className="w-2.5 h-2.5" />
                      )
                    )}
                    {(isFailed || isBlocked) && <AlertCircle className="w-2.5 h-2.5" />}
                    {s.status}
                  </span>
                </div>

                {/* Progress Bar */}
                <div className="mt-2 mb-1">
                  <div className="w-full bg-slate-100 rounded-full h-1 overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-300 ${
                        isCompleted
                          ? s.status === "No Results"
                            ? "bg-slate-400"
                            : "bg-emerald-500"
                          : isFailed
                          ? "bg-rose-500"
                          : "bg-blue-600"
                      }`}
                      style={{
                        width: isCompleted ? "100%" : `${s.progress || 0}%`,
                      }}
                    />
                  </div>
                </div>

                {/* Current Operation Message */}
                <p className="text-[11px] text-slate-500 truncate mt-1">
                  {s.currentOperation || (s.status === "Waiting" ? "Ready" : "")}
                </p>

                {/* Error Box if Failed */}
                {isFailed && s.errorMessage && (
                  <div className="mt-2 p-1.5 bg-rose-50 border border-rose-200 rounded text-[10px] text-rose-700">
                    <p className="line-clamp-2">{s.errorMessage}</p>
                  </div>
                )}
              </div>

              {/* Card Footer: Metrics & Action */}
              <div className="mt-2.5 pt-2 border-t border-slate-100 flex items-center justify-between text-[11px]">
                <div className="flex items-center gap-2.5 text-slate-500">
                  <span className="flex items-center gap-1 font-medium">
                    <Package className="w-3 h-3 text-slate-400" />
                    <span className="text-slate-800 font-semibold">
                      {s.productsExtracted || 0}
                    </span>{" "}
                    found
                  </span>

                  {s.elapsedSeconds !== undefined && s.elapsedSeconds !== null && (
                    <span className="flex items-center gap-1">
                      <Clock className="w-3 h-3 text-slate-400" />
                      <span>{s.elapsedSeconds}s</span>
                    </span>
                  )}
                </div>

                {/* View Results Button */}
                <button
                  type="button"
                  disabled={!s.productsExtracted && !isCompleted}
                  onClick={() => onViewSupplierResults(s.vendor)}
                  className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium transition-colors ${
                    s.productsExtracted > 0 || isCompleted
                      ? "bg-slate-100 hover:bg-slate-200 text-slate-800"
                      : "text-slate-300 cursor-not-allowed"
                  }`}
                >
                  <span>View</span>
                  <ArrowRight className="w-3 h-3" />
                </button>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
