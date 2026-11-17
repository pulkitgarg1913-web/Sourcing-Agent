import React from "react"
import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import ConversationPanel from "../components/ConversationPanel"

describe("ConversationPanel Component", () => {
  const dummyProfile = {
    part: "Brake Pads",
    maker: "Maruti",
    model: "Swift",
    year: "2018",
    variant: "VXi Petrol",
  }

  it("renders empty state placeholder when no messages are provided", () => {
    render(
      <ConversationPanel
        messages={[]}
        pendingQuestion={null}
        vehicleProfile={{}}
        onSendMessage={vi.fn()}
        onReplyQuestion={vi.fn()}
        isSearching={false}
        activeSupplierCount={0}
      />
    )

    expect(screen.getByText("Ready to find automotive parts")).toBeDefined()
  })

  it("renders messages for both bot and mechanic", () => {
    const messages = [
      { role: "agent", text: "Hello! What part do you need?", timestamp: "10:00 AM" },
      { role: "mechanic", text: "I need front brake pads for Maruti Swift.", timestamp: "10:01 AM" },
    ]

    render(
      <ConversationPanel
        messages={messages}
        pendingQuestion={null}
        vehicleProfile={dummyProfile}
        onSendMessage={vi.fn()}
        onReplyQuestion={vi.fn()}
        isSearching={false}
        activeSupplierCount={0}
      />
    )

    expect(screen.getByText("Hello! What part do you need?")).toBeDefined()
    expect(screen.getByText("I need front brake pads for Maruti Swift.")).toBeDefined()
    expect(screen.getByText("Part: Brake Pads")).toBeDefined()
  })

  it("calls onSendMessage when typing and submitting a message", () => {
    const onSendMock = vi.fn()
    render(
      <ConversationPanel
        messages={[]}
        pendingQuestion={null}
        vehicleProfile={{}}
        onSendMessage={onSendMock}
        onReplyQuestion={vi.fn()}
        isSearching={false}
        activeSupplierCount={0}
      />
    )

    const input = screen.getByPlaceholderText(/type part name or car details/i)
    fireEvent.change(input, { target: { value: "Oil filter for Tata Nexon" } })

    const form = input.closest("form")
    fireEvent.submit(form)

    expect(onSendMock).toHaveBeenCalledWith("Oil filter for Tata Nexon")
  })

  it("renders pending question options and triggers onReplyQuestion", () => {
    const onReplyMock = vi.fn()
    const pendingQuestion = {
      question: "Which fuel type is your Swift?",
      options: ["Petrol", "Diesel", "CNG"],
    }

    render(
      <ConversationPanel
        messages={[{ role: "agent", text: "Checking vehicle details" }]}
        pendingQuestion={pendingQuestion}
        vehicleProfile={{ maker: "Maruti", model: "Swift" }}
        onSendMessage={vi.fn()}
        onReplyQuestion={onReplyMock}
        isSearching={false}
        activeSupplierCount={0}
      />
    )

    expect(screen.getByText("Which fuel type is your Swift?")).toBeDefined()
    const petrolBtn = screen.getByRole("button", { name: "Petrol" })
    fireEvent.click(petrolBtn)

    expect(onReplyMock).toHaveBeenCalledWith("Petrol")
  })

  it("does not call scrollIntoView on message updates (prevents full page jumping)", () => {
    const scrollIntoViewSpy = vi.fn()
    window.HTMLElement.prototype.scrollIntoView = scrollIntoViewSpy

    const { rerender } = render(
      <ConversationPanel
        messages={[{ role: "agent", text: "Initial message" }]}
        pendingQuestion={null}
        vehicleProfile={dummyProfile}
        onSendMessage={vi.fn()}
        onReplyQuestion={vi.fn()}
        isSearching={false}
        activeSupplierCount={0}
      />
    )

    // Append a new assistant message
    rerender(
      <ConversationPanel
        messages={[
          { role: "agent", text: "Initial message" },
          { role: "agent", text: "New incoming message from agent" },
        ]}
        pendingQuestion={null}
        vehicleProfile={dummyProfile}
        onSendMessage={vi.fn()}
        onReplyQuestion={vi.fn()}
        isSearching={false}
        activeSupplierCount={0}
      />
    )

    // scrollIntoView must NOT have been called!
    expect(scrollIntoViewSpy).not.toHaveBeenCalled()
  })

  it("renders editable details for fuel and transmission and opens inline editor", () => {
    const onSendMock = vi.fn()
    const profileWithFilters = {
      ...dummyProfile,
      fuel: "Diesel",
      transmission: "Manual",
    }

    render(
      <ConversationPanel
        messages={[{ role: "agent", text: "Vehicle profile ready" }]}
        pendingQuestion={null}
        vehicleProfile={profileWithFilters}
        onSendMessage={onSendMock}
        onReplyQuestion={vi.fn()}
        isSearching={false}
        activeSupplierCount={0}
      />
    )

    expect(screen.getByText("Fuel: Diesel")).toBeDefined()
    expect(screen.getByText("Transmission: Manual")).toBeDefined()

    // Click Fuel badge to edit
    const fuelBadge = screen.getByText("Fuel: Diesel")
    fireEvent.click(fuelBadge)

    // Editor should appear
    expect(screen.getByText("Update fuel:")).toBeDefined()
    const editInput = screen.getByPlaceholderText("Enter new fuel")
    fireEvent.change(editInput, { target: { value: "Petrol" } })

    const saveBtn = screen.getByRole("button", { name: "Save" })
    fireEvent.click(saveBtn)

    expect(onSendMock).toHaveBeenCalledWith("Change fuel to Petrol")
  })
})
