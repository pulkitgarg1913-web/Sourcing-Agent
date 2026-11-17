import React, { useState, useEffect, useRef } from "react"
import {
  Send,
  Mic,
  MicOff,
  User,
  Bot,
  Car,
  CheckCircle,
  HelpCircle,
  ChevronDown,
  Edit2,
  Sparkles,
  ArrowRight,
  Loader2,
  X,
} from "lucide-react"
import { uploadAudioForTranscription } from "../services/api"

export default function ConversationPanel({
  messages,
  pendingQuestion,
  vehicleProfile,
  onSendMessage,
  onReplyQuestion,
  isSearching,
  activeSupplierCount,
}) {
  const [inputText, setInputText] = useState("")
  const [isRecording, setIsRecording] = useState(false)
  const [isTranscribing, setIsTranscribing] = useState(false)
  const [selectedDropdownValue, setSelectedDropdownValue] = useState("")
  const [editingField, setEditingField] = useState(null)
  const [editValue, setEditValue] = useState("")

  const messagesContainerRef = useRef(null)
  const isNearBottomRef = useRef(true)
  const userJustSentRef = useRef(false)

  const inputRef = useRef(null)
  const mediaRecorderRef = useRef(null)
  const audioChunksRef = useRef([])
  const recognitionRef = useRef(null)

  // Track if user was scrolled near bottom before update
  const handleScroll = () => {
    const el = messagesContainerRef.current
    if (!el) return
    const threshold = 120 // pixels threshold to detect if near bottom
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    isNearBottomRef.current = distanceFromBottom <= threshold
  }

  // Helper to safely scroll chat container
  const scrollChatToBottom = (smooth = true) => {
    const el = messagesContainerRef.current
    if (!el) return
    if (typeof el.scrollTo === "function") {
      try {
        el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" })
        return
      } catch (err) {}
    }
    el.scrollTop = el.scrollHeight
  }

  // Auto-scroll chat container safely without scrolling page/window
  useEffect(() => {
    const el = messagesContainerRef.current
    if (!el) return

    const wasUserAction = userJustSentRef.current
    userJustSentRef.current = false

    if (wasUserAction) {
      // User triggered action: always scroll chat container to bottom
      scrollChatToBottom(true)
      isNearBottomRef.current = true
    } else if (isNearBottomRef.current) {
      // New assistant response or update: only scroll if user was already near bottom
      scrollChatToBottom(true)
    }
    // If user scrolled up, do not force them to bottom!
  }, [messages, pendingQuestion])

  // Reset dropdown when question changes
  useEffect(() => {
    setSelectedDropdownValue("")
    if (inputRef.current) {
      inputRef.current.focus()
    }
  }, [pendingQuestion])

  // Setup Web Speech recognition as instant transcription option
  useEffect(() => {
    if (typeof window !== "undefined") {
      const SpeechRecognition =
        window.SpeechRecognition || window.webkitSpeechRecognition
      if (SpeechRecognition) {
        const reco = new SpeechRecognition()
        reco.continuous = false
        reco.interimResults = true
        reco.lang = "en-IN"

        reco.onresult = (event) => {
          let currentTranscript = ""
          for (let i = event.resultIndex; i < event.results.length; ++i) {
            currentTranscript += event.results[i][0].transcript
          }
          setInputText(currentTranscript)
        }

        reco.onerror = (e) => {
          console.warn("Web Speech error, falling back to MediaRecorder", e)
        }

        reco.onend = () => {
          setIsRecording(false)
        }

        recognitionRef.current = reco
      }
    }
  }, [])

  // Start / Stop Microphone Recording
  const handleToggleVoice = async () => {
    if (isRecording) {
      // Stop recording
      if (recognitionRef.current) {
        try {
          recognitionRef.current.stop()
        } catch (e) {}
      }

      if (
        mediaRecorderRef.current &&
        mediaRecorderRef.current.state === "recording"
      ) {
        mediaRecorderRef.current.stop()
      }
      setIsRecording(false)
      return
    }

    // Start recording
    setInputText("")
    setIsRecording(true)

    // Try starting Web Speech for live text preview
    if (recognitionRef.current) {
      try {
        recognitionRef.current.start()
      } catch (e) {
        console.warn("Could not start Web Speech API", e)
      }
    }

    // Also record raw audio with MediaRecorder for backend Gemini STT fallback
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      audioChunksRef.current = []
      const mr = new MediaRecorder(stream)

      mr.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          audioChunksRef.current.push(e.data)
        }
      }

      mr.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop())
        if (audioChunksRef.current.length > 0) {
          const blob = new Blob(audioChunksRef.current, {
            type: "audio/webm;codecs=opus",
          })
          // If input text is still empty, call backend Gemini transcription endpoint
          if (!inputRef.current?.value) {
            try {
              setIsTranscribing(true)
              const context = pendingQuestion ? pendingQuestion.question : ""
              const data = await uploadAudioForTranscription(blob, context)
              if (data && data.text) {
                setInputText(data.text)
              }
            } catch (err) {
              console.warn("Gemini transcription fallback failed", err)
            } finally {
              setIsTranscribing(false)
            }
          }
        }
      }

      mediaRecorderRef.current = mr
      mr.start(250)
    } catch (err) {
      console.warn("Could not access microphone", err)
      setIsRecording(false)
    }
  }

  // Handle Text Submission
  const handleSubmit = (e) => {
    e.preventDefault()
    const trimmed = inputText.trim()
    if (!trimmed) return

    userJustSentRef.current = true
    if (pendingQuestion) {
      onReplyQuestion(trimmed)
    } else {
      onSendMessage(trimmed)
    }
    setInputText("")
  }

  // Handle Option Click (pills / buttons)
  const handleOptionClick = (option) => {
    userJustSentRef.current = true
    onReplyQuestion(option)
    setInputText("")
  }

  // Handle Quick Edit of collected vehicle details
  const handleSaveEdit = (fieldKey) => {
    if (!editValue.trim()) {
      setEditingField(null)
      return
    }
    userJustSentRef.current = true
    // Formulate a natural clarification reply e.g. "Change year to 2021"
    const correctionMsg = `Change ${fieldKey} to ${editValue.trim()}`
    if (pendingQuestion) {
      onReplyQuestion(correctionMsg)
    } else {
      onSendMessage(correctionMsg)
    }
    setEditingField(null)
    setEditValue("")
  }

  // Determine what type of options to render
  const options = pendingQuestion?.options || []
  const isConfirmSearch = pendingQuestion?.field === "confirm_search"
  const isDropdown = options.length > 12 && !isConfirmSearch

  return (
    <div className="flex flex-col h-full bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
      {/* Panel Header */}
      <div className="px-5 py-3.5 border-b border-slate-100 flex items-center justify-between bg-slate-50/70">
        <div className="flex items-center gap-2">
          <div className="w-2.5 h-2.5 rounded-full bg-blue-600" />
          <h2 className="text-sm font-semibold text-slate-800">
            Conversational Agent
          </h2>
        </div>
        {isSearching && (
          <div className="flex items-center gap-1.5 text-xs text-blue-600 bg-blue-50 px-2 py-0.5 rounded-md font-medium border border-blue-100">
            <Loader2 className="w-3 h-3 animate-spin" />
            <span>Sourcing in progress</span>
          </div>
        )}
      </div>

      {/* Compact Vehicle Profile Summary Bar */}
      {vehicleProfile && (vehicleProfile.maker || vehicleProfile.part) && (
        <div className="bg-slate-50 border-b border-slate-200/80 px-4 py-2.5">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500 flex items-center gap-1">
              <Car className="w-3.5 h-3.5 text-slate-400" />
              Vehicle &amp; Part Profile
            </span>
            <span className="text-[11px] text-slate-400">
              Click badge to correct
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            {/* Part */}
            {vehicleProfile.part && (
              <span
                onClick={() => {
                  setEditingField("part")
                  setEditValue(vehicleProfile.part)
                }}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-semibold bg-blue-100 text-blue-800 border border-blue-200 cursor-pointer hover:bg-blue-200 transition-colors"
                title="Click to edit part"
              >
                Part: {vehicleProfile.part}
                <Edit2 className="w-2.5 h-2.5 opacity-60" />
              </span>
            )}

            {/* Maker */}
            {vehicleProfile.maker && (
              <span
                onClick={() => {
                  setEditingField("maker")
                  setEditValue(vehicleProfile.maker)
                }}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium bg-white text-slate-700 border border-slate-200 shadow-2xs cursor-pointer hover:border-slate-300 transition-colors"
                title="Click to edit make"
              >
                Make: {vehicleProfile.maker}
                <Edit2 className="w-2.5 h-2.5 opacity-50" />
              </span>
            )}

            {/* Model */}
            {vehicleProfile.model && (
              <span
                onClick={() => {
                  setEditingField("model")
                  setEditValue(vehicleProfile.model)
                }}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium bg-white text-slate-700 border border-slate-200 shadow-2xs cursor-pointer hover:border-slate-300 transition-colors"
                title="Click to edit model"
              >
                Model: {vehicleProfile.model}
                <Edit2 className="w-2.5 h-2.5 opacity-50" />
              </span>
            )}

            {/* Year */}
            {vehicleProfile.year && (
              <span
                onClick={() => {
                  setEditingField("year")
                  setEditValue(vehicleProfile.year)
                }}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium bg-white text-slate-700 border border-slate-200 shadow-2xs cursor-pointer hover:border-slate-300 transition-colors"
                title="Click to edit year"
              >
                Year: {vehicleProfile.year}
                <Edit2 className="w-2.5 h-2.5 opacity-50" />
              </span>
            )}

            {/* Variant / Modification */}
            {vehicleProfile.variant && (
              <span
                onClick={() => {
                  setEditingField("variant")
                  setEditValue(vehicleProfile.variant)
                }}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium bg-emerald-50 text-emerald-800 border border-emerald-200 cursor-pointer hover:bg-emerald-100 transition-colors"
                title="Click to edit variant"
              >
                Variant: {vehicleProfile.variant}
                <Edit2 className="w-2.5 h-2.5 opacity-60" />
              </span>
            )}

            {/* Variant Filters (Fuel, Transmission, etc.) */}
            {vehicleProfile.variant_filters &&
              Object.entries(vehicleProfile.variant_filters).map(
                ([k, v]) =>
                  v &&
                  v !== "*unknown*" && (
                    <span
                      key={k}
                      onClick={() => {
                        setEditingField(k)
                        setEditValue(v)
                      }}
                      className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium bg-slate-100 text-slate-700 border border-slate-200 cursor-pointer hover:bg-slate-200 transition-colors capitalize"
                      title={`Click to edit ${k}`}
                    >
                      {k}: {v}
                      <Edit2 className="w-2.5 h-2.5 opacity-50" />
                    </span>
                  )
              )}

            {/* Direct Transmission / Fuel / Trim fallbacks */}
            {vehicleProfile.fuel && !vehicleProfile.variant_filters?.fuel && (
              <span
                onClick={() => {
                  setEditingField("fuel")
                  setEditValue(vehicleProfile.fuel)
                }}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium bg-slate-100 text-slate-700 border border-slate-200 cursor-pointer hover:bg-slate-200 transition-colors"
                title="Click to edit fuel type"
              >
                Fuel: {vehicleProfile.fuel}
                <Edit2 className="w-2.5 h-2.5 opacity-50" />
              </span>
            )}

            {vehicleProfile.transmission && !vehicleProfile.variant_filters?.transmission && (
              <span
                onClick={() => {
                  setEditingField("transmission")
                  setEditValue(vehicleProfile.transmission)
                }}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium bg-slate-100 text-slate-700 border border-slate-200 cursor-pointer hover:bg-slate-200 transition-colors"
                title="Click to edit transmission"
              >
                Transmission: {vehicleProfile.transmission}
                <Edit2 className="w-2.5 h-2.5 opacity-50" />
              </span>
            )}

            {vehicleProfile.trim && !vehicleProfile.variant_filters?.trim && (
              <span
                onClick={() => {
                  setEditingField("trim")
                  setEditValue(vehicleProfile.trim)
                }}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium bg-slate-100 text-slate-700 border border-slate-200 cursor-pointer hover:bg-slate-200 transition-colors"
                title="Click to edit trim"
              >
                Trim: {vehicleProfile.trim}
                <Edit2 className="w-2.5 h-2.5 opacity-50" />
              </span>
            )}
          </div>

          {/* Inline Edit Modal / Popover */}
          {editingField && (
            <div className="mt-2.5 p-2.5 bg-white rounded-lg border border-blue-200 shadow-sm flex items-center gap-2">
              <span className="text-xs font-medium text-slate-600 whitespace-nowrap capitalize">
                Update {editingField}:
              </span>
              <input
                type="text"
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleSaveEdit(editingField)
                  if (e.key === "Escape") setEditingField(null)
                }}
                className="flex-1 text-xs px-2.5 py-1.5 border border-slate-200 rounded-md focus:outline-none focus:ring-1 focus:ring-blue-500"
                placeholder={`Enter new ${editingField}`}
                autoFocus
              />
              <button
                type="button"
                onClick={() => handleSaveEdit(editingField)}
                className="px-2.5 py-1.5 bg-blue-600 text-white text-xs font-medium rounded-md hover:bg-blue-700 transition-colors"
              >
                Save
              </button>
              <button
                type="button"
                onClick={() => setEditingField(null)}
                className="p-1 text-slate-400 hover:text-slate-600"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          )}
        </div>
      )}

      {/* Chat Messages Feed */}
      <div
        ref={messagesContainerRef}
        onScroll={handleScroll}
        className="flex-1 min-h-0 overflow-y-auto p-4 space-y-3.5"
      >
        {messages.length === 0 && (
          <div className="h-full flex flex-col items-center justify-center text-center p-6 text-slate-400">
            <div className="w-12 h-12 rounded-2xl bg-blue-50 text-blue-600 flex items-center justify-center mb-3 border border-blue-100">
              <Sparkles className="w-6 h-6" />
            </div>
            <h3 className="text-sm font-semibold text-slate-700">
              Ready to find automotive parts
            </h3>
            <p className="text-xs text-slate-500 max-w-sm mt-1">
              Ask in plain English or Hinglish:
              <br />
              <span className="italic font-medium text-slate-600">
                &ldquo;I need an oil filter for a Tata Harrier 2021 diesel&rdquo;
              </span>
            </p>
          </div>
        )}

        {messages.map((msg, index) => {
          const isCustomer = msg.role === "mechanic" || msg.role === "user"
          return (
            <div
              key={index}
              className={`flex items-start gap-2.5 ${
                isCustomer ? "justify-end" : "justify-start"
              }`}
            >
              {!isCustomer && (
                <div className="w-7 h-7 rounded-lg bg-blue-600 text-white flex items-center justify-center shrink-0 mt-0.5 text-xs">
                  <Bot className="w-4 h-4" />
                </div>
              )}

              <div
                className={`max-w-[82%] px-3.5 py-2.5 rounded-2xl text-xs sm:text-sm leading-relaxed ${
                  isCustomer
                    ? "bg-blue-600 text-white rounded-tr-xs shadow-xs"
                    : "bg-slate-100 text-slate-800 rounded-tl-xs border border-slate-200/60"
                }`}
              >
                <p className="whitespace-pre-wrap">{msg.text || msg.message}</p>
                {msg.timestamp && (
                  <span
                    className={`block text-[10px] mt-1 text-right ${
                      isCustomer ? "text-blue-100" : "text-slate-400"
                    }`}
                  >
                    {msg.timestamp}
                  </span>
                )}
              </div>

              {isCustomer && (
                <div className="w-7 h-7 rounded-lg bg-slate-800 text-white flex items-center justify-center shrink-0 mt-0.5 text-xs">
                  <User className="w-4 h-4" />
                </div>
              )}
            </div>
          )
        })}

        {/* Quick Starter Suggestions for One-Click Testing */}
        {messages.length === 1 && !pendingQuestion && !isSearching && (
          <div className="pt-3 pb-1 pl-9">
            <p className="text-[11px] font-medium text-slate-400 mb-2">
              Quick test examples:
            </p>
            <div className="flex flex-wrap gap-2">
              {[
                "Tata Nexon oil filter",
                "Swift front brake pads",
                "Innova Crysta clutch kit",
                "Mercedes E-Class brake pads",
              ].map((example) => (
                <button
                  key={example}
                  type="button"
                  onClick={() => {
                    userJustSentRef.current = true
                    onSendMessage(example)
                    setInputText("")
                  }}
                  className="text-xs bg-white hover:bg-blue-50 text-slate-700 hover:text-blue-700 border border-slate-200 hover:border-blue-300 rounded-lg px-3 py-1.5 transition-colors cursor-pointer text-left shadow-2xs flex items-center gap-1.5"
                >
                  <span className="text-slate-400 text-xs">🚗</span>
                  <span className="font-medium">{example}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Dynamic Follow-Up Question Container */}
        {pendingQuestion && (
          <div className="bg-blue-50/80 border border-blue-200 rounded-xl p-4 my-2 shadow-xs">
            {/* Note & Question */}
            {pendingQuestion.note && (
              <p className="text-xs font-medium text-blue-700 mb-1">
                {pendingQuestion.note}
              </p>
            )}

            <div className="flex items-start gap-2 mb-3">
              <HelpCircle className="w-4 h-4 text-blue-600 shrink-0 mt-0.5" />
              <h3 className="text-sm font-semibold text-slate-900 leading-snug">
                {pendingQuestion.question}
              </h3>
            </div>

            {/* Clear Confirmation Step Before Supplier Search Begins */}
            {isConfirmSearch ? (
              <div className="bg-white rounded-lg p-3 border border-blue-200/80 mb-2">
                <div className="flex items-center gap-2 mb-2">
                  <CheckCircle className="w-4 h-4 text-emerald-600" />
                  <span className="text-xs font-semibold text-slate-800">
                    Vehicle details verified &amp; ready
                  </span>
                </div>
                <p className="text-xs text-slate-600 mb-3">
                  Vehicle:{" "}
                  <strong className="text-slate-900">
                    {pendingQuestion.candidate || "Selected vehicle"}
                  </strong>
                  . Sourcing will run simultaneously on Boodmo and AutoDukan.
                </p>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => handleOptionClick("Confirm & Search Suppliers")}
                    className="flex-1 py-2 px-3 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs font-semibold flex items-center justify-center gap-1.5 shadow-sm transition-colors"
                  >
                    <span>Confirm &amp; Search Suppliers</span>
                    <ArrowRight className="w-3.5 h-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => handleOptionClick("Change Vehicle Details")}
                    className="py-2 px-3 bg-white hover:bg-slate-100 text-slate-700 border border-slate-300 rounded-lg text-xs font-medium transition-colors"
                  >
                    Modify
                  </button>
                </div>
              </div>
            ) : isDropdown ? (
              /* Dropdown for long option lists (e.g. Years or Variants) */
              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <div className="relative flex-1">
                    <select
                      value={selectedDropdownValue}
                      onChange={(e) => setSelectedDropdownValue(e.target.value)}
                      className="w-full text-xs bg-white border border-slate-300 rounded-lg py-2 pl-3 pr-8 text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500 appearance-none cursor-pointer"
                    >
                      <option value="">
                        -- Select {pendingQuestion.field || "an option"} ({options.length} available) --
                      </option>
                      {options.map((opt, i) => (
                        <option key={i} value={opt}>
                          {opt}
                        </option>
                      ))}
                    </select>
                    <ChevronDown className="w-4 h-4 text-slate-400 absolute right-2.5 top-2.5 pointer-events-none" />
                  </div>
                  <button
                    type="button"
                    disabled={!selectedDropdownValue}
                    onClick={() => handleOptionClick(selectedDropdownValue)}
                    className="py-2 px-3 bg-blue-600 disabled:opacity-50 hover:bg-blue-700 text-white rounded-lg text-xs font-medium transition-colors"
                  >
                    Select
                  </button>
                </div>

                {/* Also show top quick chips for faster selection */}
                <div className="flex flex-wrap gap-1.5 pt-1">
                  {options.slice(0, 8).map((opt, idx) => (
                    <button
                      key={idx}
                      type="button"
                      onClick={() => handleOptionClick(opt)}
                      className="text-[11px] font-medium bg-white hover:bg-blue-100 text-slate-700 border border-slate-200 hover:border-blue-300 rounded-md px-2.5 py-1 transition-colors"
                    >
                      {opt}
                    </button>
                  ))}
                  {options.length > 8 && (
                    <span className="text-[10px] text-slate-400 self-center">
                      +{options.length - 8} more in dropdown
                    </span>
                  )}
                </div>
              </div>
            ) : options.length > 0 ? (
              /* Selectable Chips / Option Buttons for <= 6 options */
              <div className="flex flex-wrap gap-2">
                {options.map((opt, idx) => (
                  <button
                    key={idx}
                    type="button"
                    onClick={() => handleOptionClick(opt)}
                    className="text-xs font-medium bg-white hover:bg-blue-600 hover:text-white text-slate-700 border border-slate-200 hover:border-blue-600 rounded-lg px-3 py-1.5 shadow-2xs transition-all transform active:scale-95"
                  >
                    {opt}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
        )}
      </div>

      {/* Voice Transcription Indicator / Preview Banner */}
      {isRecording && (
        <div className="bg-amber-50 border-t border-amber-200 px-4 py-2 flex items-center justify-between animate-pulse">
          <div className="flex items-center gap-2">
            <span className="w-2.5 h-2.5 rounded-full bg-rose-500 animate-ping" />
            <span className="text-xs font-semibold text-amber-900">
              Listening to speech... Speak clearly
            </span>
          </div>
          <button
            type="button"
            onClick={handleToggleVoice}
            className="text-xs text-amber-900 font-medium underline hover:text-amber-700"
          >
            Stop Listening
          </button>
        </div>
      )}

      {isTranscribing && (
        <div className="bg-blue-50 border-t border-blue-200 px-4 py-1.5 flex items-center gap-2 text-xs text-blue-700">
          <Loader2 className="w-3 h-3 animate-spin" />
          <span>Transcribing voice with Gemini STT...</span>
        </div>
      )}

      {/* Customer Input Form */}
      <form
        onSubmit={handleSubmit}
        className="p-3 border-t border-slate-200 bg-white shrink-0"
      >
        <div className="relative flex items-center rounded-xl border border-slate-300 bg-white focus-within:border-blue-500 focus-within:ring-2 focus-within:ring-blue-100 transition-all">
          <input
            ref={inputRef}
            type="text"
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            placeholder={
              pendingQuestion
                ? "Type your answer or select an option above..."
                : "Type part name or car details (e.g. Harrier oil filter)..."
            }
            className="flex-1 text-xs sm:text-sm px-3.5 py-2.5 bg-transparent border-none focus:outline-none text-slate-800 placeholder:text-slate-400"
          />

          <div className="flex items-center gap-1 pr-1.5">
            {/* Microphone Button */}
            <button
              type="button"
              onClick={handleToggleVoice}
              title={isRecording ? "Stop voice input" : "Voice input (mic)"}
              className={`p-2 rounded-lg transition-colors ${
                isRecording
                  ? "bg-rose-500 text-white shadow-sm"
                  : "text-slate-500 hover:text-blue-600 hover:bg-slate-100"
              }`}
            >
              {isRecording ? (
                <MicOff className="w-4 h-4 animate-bounce" />
              ) : (
                <Mic className="w-4 h-4" />
              )}
            </button>

            {/* Send Button */}
            <button
              type="submit"
              disabled={!inputText.trim()}
              className="p-2 rounded-lg bg-blue-600 disabled:opacity-40 text-white hover:bg-blue-700 transition-colors shadow-2xs"
            >
              <Send className="w-4 h-4" />
            </button>
          </div>
        </div>

        <div className="flex items-center justify-between mt-1.5 px-1 text-[11px] text-slate-400">
          <span>Press Enter to send</span>
          <span>Dual search: Boodmo &amp; AutoDukan</span>
        </div>
      </form>
    </div>
  )
}
