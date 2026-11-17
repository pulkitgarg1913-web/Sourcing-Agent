import React from "react"
import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import App from "../App"

// Mock api service to prevent real network calls
vi.mock("../services/api", () => ({
  startChat: vi.fn().mockResolvedValue({ status: "started" }),
  replyChat: vi.fn().mockResolvedValue({ status: "replied" }),
  resetSession: vi.fn().mockResolvedValue({ status: "reset" }),
  subscribeToEvents: vi.fn().mockImplementation((threadId, onEvent, onError, onOpen) => {
    return () => {}
  }),
  getSessionHistory: vi.fn().mockResolvedValue({ events: [] }),
  uploadAudioForTranscription: vi.fn().mockResolvedValue({ text: "" }),
}))

describe("Two-Panel App Workspace Layout", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("renders both Conversational Agent panel and Live Extraction panel", () => {
    render(<App />)

    // Left Panel header
    expect(screen.getByText("Conversational Agent")).toBeDefined()

    // Right Panel headers & sections
    expect(screen.getByText("Supplier Search Status")).toBeDefined()
    expect(screen.getByText("Execution Activity")).toBeDefined()
    expect(screen.getByText("Procurement Recommendation")).toBeDefined()

    // Both supplier catalogs rendered separately
    expect(screen.getByText("Boodmo Verified Catalog")).toBeDefined()
    expect(screen.getByText("AutoDukan Verified Catalog")).toBeDefined()

    // Fixed chat input presence
    expect(
      screen.getByPlaceholderText(/Type part name or car details/i)
    ).toBeDefined()
  })

  it("never calls scrollIntoView when sending a message", async () => {
    const scrollIntoViewSpy = vi.fn()
    window.HTMLElement.prototype.scrollIntoView = scrollIntoViewSpy

    render(<App />)

    const input = screen.getByPlaceholderText(/Type part name or car details/i)
    fireEvent.change(input, { target: { value: "Harrier Brake Pads" } })

    const form = input.closest("form")
    fireEvent.submit(form)

    // Ensure the message appears in the chat
    expect(screen.getByText("Harrier Brake Pads")).toBeDefined()

    // CRITICAL: scrollIntoView must NOT be invoked on the page/window
    expect(scrollIntoViewSpy).not.toHaveBeenCalled()
  })

  it("allows switching mobile tabs between Chat and Results", () => {
    render(<App />)

    const chatTabBtn = screen.getByRole("button", { name: /Agent Chat/i })
    const resultsTabBtn = screen.getByRole("button", { name: /Extracted Results/i })

    expect(chatTabBtn).toBeDefined()
    expect(resultsTabBtn).toBeDefined()

    // Switch to Results tab
    fireEvent.click(resultsTabBtn)
    expect(screen.getByText("Supplier Search Status")).toBeDefined()

    // Switch back to Chat tab
    fireEvent.click(chatTabBtn)
    expect(screen.getByText("Conversational Agent")).toBeDefined()
  })

  it("isolates supplier sections when filtering by specific supplier", () => {
    render(<App />)

    // Click Boodmo Only filter
    const boodmoOnlyBtn = screen.getByRole("button", { name: /Boodmo Only/i })
    fireEvent.click(boodmoOnlyBtn)

    expect(screen.getByText("Boodmo Verified Catalog")).toBeDefined()
    expect(screen.queryByText("AutoDukan Verified Catalog")).toBeNull()

    // Click AutoDukan Only filter
    const autoDukanOnlyBtn = screen.getByRole("button", { name: /AutoDukan Only/i })
    fireEvent.click(autoDukanOnlyBtn)

    expect(screen.queryByText("Boodmo Verified Catalog")).toBeNull()
    expect(screen.getByText("AutoDukan Verified Catalog")).toBeDefined()

    // Click All Suppliers
    const allSuppliersBtn = screen.getByRole("button", { name: /All Suppliers/i })
    fireEvent.click(allSuppliersBtn)

    expect(screen.getByText("Boodmo Verified Catalog")).toBeDefined()
    expect(screen.getByText("AutoDukan Verified Catalog")).toBeDefined()
  })
})
