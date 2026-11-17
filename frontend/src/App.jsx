import React, { useState, useEffect, useCallback, useRef } from "react"
import { MessageSquare, Layers } from "lucide-react"
import Header from "./components/Header"
import ConversationPanel from "./components/ConversationPanel"
import SupplierMonitor from "./components/SupplierMonitor"
import ActivityLog from "./components/ActivityLog"
import ProductResults from "./components/ProductResults"
import NotificationToast from "./components/NotificationToast"
import {
  startChat,
  replyChat,
  resetSession,
  subscribeToEvents,
  getSessionHistory,
} from "./services/api"

const INITIAL_SUPPLIERS = [
  {
    id: "boodmo",
    vendor: "boodmo",
    name: "Boodmo",
    status: "Waiting",
    progress: 0,
    currentOperation: "Waiting for vehicle specification",
    productsExtracted: 0,
    elapsedSeconds: null,
    errorMessage: null,
  },
  {
    id: "autodukan",
    vendor: "autodukan",
    name: "AutoDukan",
    status: "Waiting",
    progress: 0,
    currentOperation: "Waiting for vehicle specification",
    productsExtracted: 0,
    elapsedSeconds: null,
    errorMessage: null,
  },
]

const getListingCompositeKey = (l) => {
  const vendor = (l.vendor || l.supplier || l.supplier_name || "").toLowerCase().trim()
  const partNo = (l.part_number || l.partNumber || "").toLowerCase().trim()
  const title = (l.title || "").toLowerCase().trim()
  const price = l.price !== null && l.price !== undefined ? l.price : ""
  const url = (l.url || "").trim()
  const isGenericSearch = !url || url.includes("products-list?search=") || url.includes("/search?")
  const urlKey = isGenericSearch ? "" : url
  if (partNo && vendor) {
    return `${vendor}::${partNo}::${title}`
  }
  return `${vendor}::${title}::${price}::${urlKey}`
}

const normalizeListingData = (l, defaultVendor = "") => {
  const rawVendor = (l.vendor || defaultVendor || l.supplier || l.supplier_name || "").toLowerCase()
  const vendor = rawVendor.includes("autodukan")
    ? "autodukan"
    : rawVendor.includes("boodmo")
    ? "boodmo"
    : (l.url && l.url.includes("autodukan.com"))
    ? "autodukan"
    : "boodmo"
  const supplier = vendor === "autodukan" ? "AutoDukan" : "Boodmo"
  return {
    ...l,
    vendor,
    supplier: l.supplier || supplier,
    supplier_name: l.supplier_name || supplier,
    seller: l.seller || l.best_offer?.seller || (vendor === "boodmo" ? "Authorized Supplier Hub" : "AutoDukan Distributor"),
    dispatch: l.dispatch || l.best_offer?.dispatch || "24–48 hours",
  }
}

export default function App() {
  const [threadId, setThreadId] = useState(() => crypto.randomUUID())
  const [connected, setConnected] = useState(false)
  const [headless, setHeadless] = useState(true)

  const [messages, setMessages] = useState([
    {
      role: "agent",
      text: "Hello! I am your AI Automotive Sourcing Agent. Which part do you need, and for which car?",
      timestamp: new Date().toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      }),
    },
  ])

  const [pendingQuestion, setPendingQuestion] = useState(null)
  const [vehicleProfile, setVehicleProfile] = useState(null)
  const [suppliers, setSuppliers] = useState(INITIAL_SUPPLIERS)
  const [listings, setListings] = useState([])
  const [logs, setLogs] = useState([])
  const [notifications, setNotifications] = useState([])
  const [activeFilter, setActiveFilter] = useState("all")
  const [overallStatus, setOverallStatus] = useState("Idle")
  const [isSearching, setIsSearching] = useState(false)
  const [mobileTab, setMobileTab] = useState("chat") // "chat" | "results"

  const resultsScrollRef = useRef(null)
  const unsubscribeRef = useRef(null)
  const seenEventIdsRef = useRef(new Set())
  const historyIndexRef = useRef(0)

  // Process Real-Time SSE Events
  const handleIncomingEvent = useCallback((event) => {
    if (!event) return
    const eventId =
      event.id ||
      `${event.type}_${event.timestamp || ""}_${event.message || (event.pending && event.pending.question) || (event.profile && event.profile.part) || ""}`
    if (seenEventIdsRef.current.has(eventId)) {
      return
    }
    seenEventIdsRef.current.add(eventId)

    const timeStr =
      event.timestamp ||
      new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })

    switch (event.type) {
      case "connection_established":
        setConnected(true)
        break

      case "log_event":
        setLogs((prev) => [
          ...prev,
          {
            timestamp: timeStr,
            message: event.message,
            level: event.level || "info",
          },
        ])
        break

      case "agent_message":
        setMessages((prev) => [
          ...prev,
          {
            role: "agent",
            text: event.message,
            timestamp: timeStr,
          },
        ])
        break

      case "need_input":
        setPendingQuestion(event.pending)
        if (event.profile) {
          setVehicleProfile(event.profile)
        }
        setIsSearching(false)
        if (event.pending?.question) {
          setMessages((prev) => {
            const last = prev[prev.length - 1]
            if (
              last &&
              (last.text === event.pending.question ||
                last.message === event.pending.question)
            ) {
              return prev
            }
            return [
              ...prev,
              {
                role: "agent",
                text: event.pending.question,
                timestamp: timeStr,
              },
            ]
          })
        }
        break

      case "vehicle_profile_update":
        if (event.profile) {
          setVehicleProfile(event.profile)
        }
        break

      case "supplier_start":
      case "adapter_progress":
        setSuppliers((prev) =>
          prev.map((s) => {
            if (s.vendor === event.vendor) {
              return {
                ...s,
                status: event.status || s.status,
                progress:
                  event.progress !== undefined ? event.progress : s.progress,
                currentOperation: event.message || s.currentOperation,
              }
            }
            return s
          })
        )
        setOverallStatus("Searching")
        break

      case "supplier_result":
      case "adapter_completed": {
        const prodCount = event.products_found !== undefined ? event.products_found : (event.product_count || 0)
        // 1. Update supplier status
        setSuppliers((prev) =>
          prev.map((s) => {
            if (s.vendor === event.vendor) {
              return {
                ...s,
                status: "Completed",
                progress: 100,
                currentOperation: `Completed: ${prodCount} parts found`,
                productsExtracted: prodCount,
                elapsedSeconds: event.elapsed_seconds,
              }
            }
            return s
          })
        )

        // 2. Incremental Product Results Addition!
        if (event.results && event.results.length > 0) {
          setListings((prevListings) => {
            const existingKeys = new Set(prevListings.map(getListingCompositeKey))
            const normalizedItems = event.results.map((l) => normalizeListingData(l, event.vendor))
            const newItems = []
            for (const item of normalizedItems) {
              const key = getListingCompositeKey(item)
              if (!existingKeys.has(key)) {
                existingKeys.add(key)
                newItems.push(item)
              }
            }
            return [...prevListings, ...newItems]
          })
        }

        // 3. Show Completion Notification Toast
        setNotifications((prev) => [
          ...prev,
          {
            id: `${event.vendor}-${Date.now()}`,
            supplier: event.supplier,
            vendor: event.vendor,
            status: "completed",
            products_found: prodCount,
            elapsed_seconds: event.elapsed_seconds,
          },
        ])
        break
      }

      case "supplier_error":
      case "adapter_failed":
        // Update failed supplier
        setSuppliers((prev) =>
          prev.map((s) => {
            if (s.vendor === event.vendor) {
              return {
                ...s,
                status: "Failed",
                progress: 100,
                currentOperation: `Failed: ${event.error}`,
                errorMessage: event.error,
                elapsedSeconds: event.elapsed_seconds,
              }
            }
            return s
          })
        )

        // Show Failure Notification Toast
        setNotifications((prev) => [
          ...prev,
          {
            id: `${event.vendor}-${Date.now()}`,
            supplier: event.supplier,
            vendor: event.vendor,
            status: "failed",
            error: event.error,
            elapsed_seconds: event.elapsed_seconds,
          },
        ])
        break

      case "complete":
      case "search_summary":
        if (event.summary) {
          setMessages((prev) => [
            ...prev,
            {
              role: "agent",
              text: event.summary,
              timestamp: timeStr,
            },
          ])
        }
        if (event.outcome?.listings && event.outcome.listings.length > 0) {
          setListings((prevListings) => {
            const existingKeys = new Set(prevListings.map(getListingCompositeKey))
            const normalizedItems = event.outcome.listings.map((l) => normalizeListingData(l))
            const newItems = []
            for (const item of normalizedItems) {
              const key = getListingCompositeKey(item)
              if (!existingKeys.has(key)) {
                existingKeys.add(key)
                newItems.push(item)
              }
            }
            return [...prevListings, ...newItems]
          })
        }
        if (event.outcome?.vendor_outcomes && event.outcome.vendor_outcomes.length > 0) {
          // Fallback merge: ensure any listings inside vendor_outcomes are also captured
          const allVoListings = event.outcome.vendor_outcomes.flatMap((vo) =>
            (vo.listings || []).map((l) => normalizeListingData(l, vo.vendor))
          )
          if (allVoListings.length > 0) {
            setListings((prevListings) => {
              const existingKeys = new Set(prevListings.map(getListingCompositeKey))
              const newItems = []
              for (const item of allVoListings) {
                const key = getListingCompositeKey(item)
                if (!existingKeys.has(key)) {
                  existingKeys.add(key)
                  newItems.push(item)
                }
              }
              return [...prevListings, ...newItems]
            })
          }

          setSuppliers((prev) =>
            prev.map((s) => {
              const vo = event.outcome.vendor_outcomes.find((v) => v.vendor === s.vendor)
              if (vo) {
                const count = (vo.listings || []).length
                return {
                  ...s,
                  status: "Completed",
                  progress: 100,
                  currentOperation: `Completed: ${count} parts found`,
                  productsExtracted: count,
                  elapsedSeconds: 0.1,
                }
              }
              return s
            })
          )
        }
        setPendingQuestion(null)
        setOverallStatus("Completed")
        setIsSearching(false)
        break

      case "error":
        setOverallStatus("Failed")
        setIsSearching(false)
        break

      default:
        break
    }
  }, [])

  // Establish SSE Connection
  useEffect(() => {
    if (unsubscribeRef.current) {
      unsubscribeRef.current()
    }

    const unsub = subscribeToEvents(
      threadId,
      (event) => {
        setConnected(true)
        handleIncomingEvent(event)
      },
      (err) => {
        setConnected(false)
      },
      () => {
        setConnected(true)
      }
    )

    unsubscribeRef.current = unsub
    return () => {
      if (unsub) unsub()
    }
  }, [threadId, handleIncomingEvent])

  // Real-time Event Poll Fallback: guarantees events are received even if a proxy buffers SSE chunks
  useEffect(() => {
    historyIndexRef.current = 0
    seenEventIdsRef.current.clear()

    const interval = setInterval(async () => {
      try {
        const data = await getSessionHistory(threadId, historyIndexRef.current)
        if (data && data.events && data.events.length > 0) {
          historyIndexRef.current += data.events.length
          setConnected(true)
          for (const ev of data.events) {
            handleIncomingEvent(ev)
          }
        }
      } catch (err) {
        // silent fallback
      }
    }, 1200)

    return () => clearInterval(interval)
  }, [threadId, handleIncomingEvent])

  // Send initial message
  const handleSendMessage = async (text) => {
    const timeStr = new Date().toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    })

    setMessages((prev) => [
      ...prev,
      {
        role: "mechanic",
        text,
        timestamp: timeStr,
      },
    ])

    setIsSearching(true)
    setOverallStatus("In Progress")

    try {
      await startChat({
        message: text,
        threadId,
        headless,
      })
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          role: "agent",
          text: `Error connecting to backend: ${err.message}`,
          timestamp: timeStr,
        },
      ])
      setIsSearching(false)
      setOverallStatus("Failed")
    }
  }

  // Reply to follow-up question
  const handleReplyQuestion = async (answer) => {
    const timeStr = new Date().toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    })

    setMessages((prev) => [
      ...prev,
      {
        role: "mechanic",
        text: answer,
        timestamp: timeStr,
      },
    ])

    setPendingQuestion(null)
    setIsSearching(true)

    try {
      await replyChat({
        threadId,
        answer,
      })
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          role: "agent",
          text: `Error sending answer: ${err.message}`,
          timestamp: timeStr,
        },
      ])
      setIsSearching(false)
    }
  }

  // Reset Session
  const handleReset = async () => {
    await resetSession(threadId)
    seenEventIdsRef.current.clear()
    historyIndexRef.current = 0
    const newId = crypto.randomUUID()
    setThreadId(newId)
    setMessages([
      {
        role: "agent",
        text: "Session reset. Which part do you need, and for which car?",
        timestamp: new Date().toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
        }),
      },
    ])
    setPendingQuestion(null)
    setVehicleProfile(null)
    setSuppliers(INITIAL_SUPPLIERS)
    setListings([])
    setLogs([])
    setNotifications([])
    setActiveFilter("all")
    setOverallStatus("Idle")
    setIsSearching(false)
  }

  // View Results & Auto-filter by supplier without scrolling window
  const handleViewResults = (vendor) => {
    setActiveFilter(vendor)
    setMobileTab("results")
    setTimeout(() => {
      const vendorEl = document.getElementById(`supplier-section-${vendor}`)
      const rightScrollEl = resultsScrollRef.current
      if (vendorEl && rightScrollEl) {
        const topPos = vendorEl.offsetTop - rightScrollEl.offsetTop
        if (typeof rightScrollEl.scrollTo === "function") {
          rightScrollEl.scrollTo({ top: Math.max(0, topPos - 12), behavior: "smooth" })
        } else {
          rightScrollEl.scrollTop = Math.max(0, topPos - 12)
        }
      }
    }, 50)
  }

  // Dismiss Toast Notification
  const handleDismissNotification = (id) => {
    setNotifications((prev) => prev.filter((n) => n.id !== id))
  }

  // Summary Metrics Calculation
  const completedSuppliers = suppliers.filter(
    (s) => s.status === "Completed"
  ).length
  const failedSuppliers = suppliers.filter((s) => s.status === "Failed").length
  const totalVerifiedPrices = listings.filter(
    (l) => l.available === true && l.price !== null
  ).length

  return (
    <div className="h-screen max-h-screen overflow-hidden flex flex-col bg-slate-50 text-slate-900">
      {/* Top Navigation */}
      <Header
        threadId={threadId}
        isSearching={isSearching}
        headless={headless}
        setHeadless={setHeadless}
        onReset={handleReset}
        connected={connected}
      />

      {/* Mobile / Tablet Responsive Tab Switcher (Visible on < lg) */}
      <div className="lg:hidden flex items-center justify-around border-b border-slate-200 bg-white px-2 py-1.5 shrink-0 text-xs">
        <button
          type="button"
          onClick={() => setMobileTab("chat")}
          className={`flex items-center gap-2 px-4 py-1.5 rounded-lg font-semibold transition-all cursor-pointer ${
            mobileTab === "chat"
              ? "bg-blue-600 text-white shadow-xs"
              : "text-slate-600 hover:bg-slate-100"
          }`}
        >
          <MessageSquare className="w-4 h-4" />
          <span>Agent Chat</span>
          {pendingQuestion && (
            <span className="w-2 h-2 rounded-full bg-amber-400 animate-ping" />
          )}
        </button>
        <button
          type="button"
          onClick={() => setMobileTab("results")}
          className={`flex items-center gap-2 px-4 py-1.5 rounded-lg font-semibold transition-all cursor-pointer ${
            mobileTab === "results"
              ? "bg-blue-600 text-white shadow-xs"
              : "text-slate-600 hover:bg-slate-100"
          }`}
        >
          <Layers className="w-4 h-4" />
          <span>Extracted Results</span>
          <span
            className={`px-1.5 py-0.2 rounded-full text-[10px] font-bold ${
              mobileTab === "results"
                ? "bg-blue-700 text-white"
                : "bg-slate-200 text-slate-700"
            }`}
          >
            {listings.length}
          </span>
        </button>
      </div>

      {/* Main Two-Panel Workspace Dashboard */}
      <main className="flex-1 min-h-0 w-full max-w-[1920px] mx-auto p-3 sm:p-4 flex flex-col lg:flex-row gap-4 overflow-hidden">
        {/* Left Panel: Conversational Agent (38% width on desktop) */}
        <div
          className={`w-full lg:w-[38%] xl:w-[37%] 2xl:w-[35%] h-full flex flex-col min-h-0 ${
            mobileTab === "chat" ? "flex" : "hidden lg:flex"
          }`}
        >
          <ConversationPanel
            messages={messages}
            pendingQuestion={pendingQuestion}
            vehicleProfile={vehicleProfile}
            onSendMessage={handleSendMessage}
            onReplyQuestion={handleReplyQuestion}
            isSearching={isSearching}
            activeSupplierCount={suppliers.length}
          />
        </div>

        {/* Right Panel: Live Extraction & Results (62% width on desktop) */}
        <div
          className={`flex-1 h-full min-h-0 flex flex-col overflow-hidden ${
            mobileTab === "results" ? "flex" : "hidden lg:flex"
          }`}
        >
          {/* Top Section: Supplier Monitor & Collapsible Activity Logs */}
          <div className="shrink-0 space-y-3 mb-3">
            {/* Live Supplier Search Monitor Cards */}
            <SupplierMonitor
              suppliers={suppliers}
              onViewSupplierResults={handleViewResults}
              activeFilter={activeFilter}
            />

            {/* Real-time Activity Log and Summary Counters */}
            <ActivityLog
              logs={logs}
              totalSuppliers={suppliers.length}
              completedSuppliers={completedSuppliers}
              failedSuppliers={failedSuppliers}
              totalProducts={listings.length}
              totalVerifiedPrices={totalVerifiedPrices}
              overallStatus={overallStatus}
            />
          </div>

          {/* Dedicated Independently Scrollable Results Area */}
          <div
            ref={resultsScrollRef}
            className="flex-1 min-h-0 overflow-y-auto space-y-4 pr-1"
          >
            <ProductResults
              listings={listings}
              suppliers={suppliers}
              activeFilter={activeFilter}
              onSelectFilter={setActiveFilter}
            />
          </div>
        </div>
      </main>

      {/* Toast Notifications */}
      <NotificationToast
        notifications={notifications}
        onDismiss={handleDismissNotification}
        onViewResults={handleViewResults}
      />
    </div>
  )
}
