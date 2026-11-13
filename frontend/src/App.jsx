import React, { useState, useEffect, useCallback, useRef } from "react"
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

      case "adapter_completed":
        // 1. Update supplier status
        setSuppliers((prev) =>
          prev.map((s) => {
            if (s.vendor === event.vendor) {
              return {
                ...s,
                status: "Completed",
                progress: 100,
                currentOperation: `Completed: ${event.products_found} parts found`,
                productsExtracted: event.products_found,
                elapsedSeconds: event.elapsed_seconds,
              }
            }
            return s
          })
        )

        // 2. Incremental Product Results Addition!
        if (event.results && event.results.length > 0) {
          setListings((prevListings) => {
            // Merge without duplicates by URL or part number
            const existingUrls = new Set(prevListings.map((l) => l.url))
            const newItems = event.results.filter((l) => !existingUrls.has(l.url))
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
            products_found: event.products_found,
            elapsed_seconds: event.elapsed_seconds,
          },
        ])
        break

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

  // View Results & Auto-filter by supplier
  const handleViewResults = (vendor) => {
    setActiveFilter(vendor)
    const el = document.getElementById("product-results-section")
    if (el) {
      el.scrollIntoView({ behavior: "smooth" })
    }
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
    <div className="min-h-screen bg-slate-50 flex flex-col text-slate-900">
      {/* Top Navigation */}
      <Header
        threadId={threadId}
        isSearching={isSearching}
        headless={headless}
        setHeadless={setHeadless}
        onReset={handleReset}
        connected={connected}
      />

      {/* Main Dashboard Layout */}
      <main className="flex-1 max-w-7xl w-full mx-auto p-4 sm:p-6 space-y-6">
        {/* Top Split Area: Conversation Panel (Left) & Monitor/Logs (Right) */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
          {/* Conversation Panel (5 columns on desktop) */}
          <div className="lg:col-span-5 h-[620px] flex flex-col">
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

          {/* Sourcing Monitor & Activity Logs (7 columns on desktop) */}
          <div className="lg:col-span-7 flex flex-col gap-6">
            {/* Live Supplier Search Monitor */}
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
        </div>

        {/* Full-width Product Results Dashboard */}
        <ProductResults
          listings={listings}
          suppliers={suppliers}
          activeFilter={activeFilter}
          onSelectFilter={setActiveFilter}
        />
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
