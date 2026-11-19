import React from "react"
import { RefreshCw, Eye, EyeOff, Wrench, BarChart3, Bot } from "lucide-react"

export default function Header({
  threadId,
  isSearching,
  headless,
  setHeadless,
  onReset,
  connected,
  currentView = "agent",
  setCurrentView,
}) {
  return (
    <header className="border-b border-slate-200 bg-white sticky top-0 z-30">
      <div className="max-w-[1920px] mx-auto px-4 sm:px-6 h-16 flex items-center justify-between">
        {/* Brand */}
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-blue-600 flex items-center justify-center text-white shadow-xs">
            <Wrench className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="font-semibold text-slate-900 text-base tracking-tight">
                AutoParts Sourcing
              </h1>
              <span className="text-[11px] font-medium bg-slate-100 text-slate-600 px-2 py-0.5 rounded-full border border-slate-200">
                Boodmo &amp; AutoDukan
              </span>
            </div>
            <p className="text-xs text-slate-500 hidden sm:block">
              Conversational parts discovery &amp; price comparison
            </p>
          </div>
        </div>

        {/* View Switcher: Sourcing Agent vs Performance Analytics */}
        {setCurrentView && (
          <div className="flex items-center bg-slate-100 p-1 rounded-xl border border-slate-200">
            <button
              type="button"
              onClick={() => setCurrentView("agent")}
              className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all cursor-pointer ${
                currentView === "agent"
                  ? "bg-white text-blue-600 shadow-xs"
                  : "text-slate-600 hover:text-slate-900"
              }`}
            >
              <Bot className="w-3.5 h-3.5" />
              <span>Sourcing Agent</span>
            </button>
            <button
              type="button"
              onClick={() => setCurrentView("analytics")}
              className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all cursor-pointer ${
                currentView === "analytics"
                  ? "bg-white text-indigo-600 shadow-xs"
                  : "text-slate-600 hover:text-slate-900"
              }`}
            >
              <BarChart3 className="w-3.5 h-3.5" />
              <span>Performance Analytics</span>
            </button>
          </div>
        )}

        {/* Controls & Status */}
        <div className="flex items-center gap-2.5">
          {/* Connection status */}
          <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium border bg-slate-50 text-slate-600 border-slate-200">
            <span
              className={`w-2 h-2 rounded-full ${
                connected ? "bg-emerald-500" : "bg-amber-400"
              }`}
            />
            <span className="hidden md:inline">
              {connected ? "Live Stream Active" : "Connecting..."}
            </span>
          </div>

          {/* Browser Mode Toggle */}
          <button
            type="button"
            onClick={() => setHeadless(!headless)}
            className={`flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded-md border transition-colors cursor-pointer ${
              headless
                ? "bg-slate-50 text-slate-700 border-slate-200 hover:bg-slate-100"
                : "bg-blue-50 text-blue-700 border-blue-200 hover:bg-blue-100"
            }`}
            title="Toggle between background browser and visible browser window"
          >
            {headless ? (
              <>
                <EyeOff className="w-3.5 h-3.5 text-slate-400" />
                <span className="hidden sm:inline">Headless</span>
              </>
            ) : (
              <>
                <Eye className="w-3.5 h-3.5 text-blue-600" />
                <span className="hidden sm:inline">Visible</span>
              </>
            )}
          </button>

          {/* Reset button */}
          <button
            type="button"
            onClick={onReset}
            className="flex items-center gap-1.5 px-3 py-1 text-xs font-medium text-slate-700 bg-white border border-slate-200 rounded-md hover:bg-slate-50 transition-colors shadow-2xs cursor-pointer"
          >
            <RefreshCw className="w-3.5 h-3.5 text-slate-500" />
            <span>New Search</span>
          </button>
        </div>
      </div>
    </header>
  )
}
