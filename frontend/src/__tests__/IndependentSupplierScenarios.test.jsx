import React from "react"
import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react"
import ProductResults from "../components/ProductResults"
import App from "../App"
import * as api from "../services/api"

// Mock API service for App integration tests
vi.mock("../services/api", () => ({
  startChat: vi.fn().mockResolvedValue({ status: "started" }),
  replyChat: vi.fn().mockResolvedValue({ status: "replied" }),
  resetSession: vi.fn().mockResolvedValue({ status: "reset" }),
  subscribeToEvents: vi.fn(),
  getSessionHistory: vi.fn().mockResolvedValue({ events: [] }),
  uploadAudioForTranscription: vi.fn().mockResolvedValue({ text: "" }),
}))

describe("Independent Supplier Scenarios (7 Acceptance Tests)", () => {
  let eventCallback = null

  beforeEach(() => {
    vi.clearAllMocks()
    api.subscribeToEvents.mockImplementation((threadId, onEvent) => {
      eventCallback = onEvent
      return () => { eventCallback = null }
    })
  })

  // Test 1: Boodmo Returns 2, AutoDukan Returns 0
  it("Scenario 1: Boodmo Returns 2, AutoDukan Returns 0 -> Total is 2", () => {
    const suppliers = [
      { id: "boodmo", vendor: "boodmo", name: "Boodmo", status: "Completed", productsExtracted: 2 },
      { id: "autodukan", vendor: "autodukan", name: "AutoDukan", status: "No Results", productsExtracted: 0 },
    ]
    const boodmoListings = [
      {
        title: "Hyundai Creta LED Headlight Unit RH",
        brand: "Hyundai (OEM)",
        part_number: "92102-M1000",
        price: 5800,
        vendor: "boodmo",
        available: true,
        fitment: "verified",
        relevance: "EXACT_MATCH",
      },
      {
        title: "Hyundai Creta LED Headlight Unit LH",
        brand: "Hyundai (OEM)",
        part_number: "92101-M1000",
        price: 5800,
        vendor: "boodmo",
        available: true,
        fitment: "verified",
        relevance: "EXACT_MATCH",
      },
    ]
    const autodukanListings = []

    render(
      <ProductResults
        boodmoListings={boodmoListings}
        autodukanListings={autodukanListings}
        suppliers={suppliers}
        activeFilter="all"
        onSelectFilter={vi.fn()}
      />
    )

    // 1. Verify Boodmo shows 2 products
    expect(screen.getByText("Hyundai Creta LED Headlight Unit RH")).toBeDefined()
    expect(screen.getByText("Hyundai Creta LED Headlight Unit LH")).toBeDefined()
    expect(screen.getByText("92102-M1000")).toBeDefined()
    expect(screen.getByText("92101-M1000")).toBeDefined()

    // 2. Verify AutoDukan shows dedicated empty state
    expect(screen.getByText("0 products found.")).toBeDefined()
    expect(screen.getByText(/No matching products found for this vehicle and part/i)).toBeDefined()

    // 3. Verify supplier navigation counts display Total 2, Boodmo 2, AutoDukan 0
    expect(screen.getByText(/All Suppliers \(Separated\) \(2\)/i)).toBeDefined()
    expect(screen.getByText(/Boodmo Only \(2\)/i)).toBeDefined()
    expect(screen.getByText(/AutoDukan Only \(0\)/i)).toBeDefined()
  })

  // Test 2: Boodmo Returns 0, AutoDukan Returns 3
  it("Scenario 2: Boodmo Returns 0, AutoDukan Returns 3 -> Total is 3", () => {
    const suppliers = [
      { id: "boodmo", vendor: "boodmo", name: "Boodmo", status: "No Results", productsExtracted: 0 },
      { id: "autodukan", vendor: "autodukan", name: "AutoDukan", status: "Completed", productsExtracted: 3 },
    ]
    const boodmoListings = []
    const autodukanListings = [
      {
        title: "AutoDukan Creta Alternator Unit A",
        brand: "Bosch",
        part_number: "AD-ALT-01",
        price: 4200,
        vendor: "autodukan",
        available: true,
        fitment: "verified",
        relevance: "EXACT_MATCH",
      },
      {
        title: "AutoDukan Creta Alternator Unit B",
        brand: "Lucas",
        part_number: "AD-ALT-02",
        price: 4500,
        vendor: "autodukan",
        available: true,
        fitment: "verified",
        relevance: "EXACT_MATCH",
      },
      {
        title: "AutoDukan Creta Alternator Pulley",
        brand: "INA",
        part_number: "AD-ALT-03",
        price: 900,
        vendor: "autodukan",
        available: true,
        fitment: "verified",
        relevance: "COMPONENT",
      },
    ]

    render(
      <ProductResults
        boodmoListings={boodmoListings}
        autodukanListings={autodukanListings}
        suppliers={suppliers}
        activeFilter="all"
        onSelectFilter={vi.fn()}
      />
    )

    // AutoDukan has 3 products
    expect(screen.getByText("AutoDukan Creta Alternator Unit A")).toBeDefined()
    expect(screen.getByText("AutoDukan Creta Alternator Unit B")).toBeDefined()
    expect(screen.getByText("AutoDukan Creta Alternator Pulley")).toBeDefined()

    // Boodmo has 0 products
    expect(screen.getByText("0 products found.")).toBeDefined()

    // Total count is 3
    expect(screen.getByText(/All Suppliers \(Separated\) \(3\)/i)).toBeDefined()
    expect(screen.getByText(/Boodmo Only \(0\)/i)).toBeDefined()
    expect(screen.getByText(/AutoDukan Only \(3\)/i)).toBeDefined()
  })

  // Test 3: Both Return Products (Boodmo = 2, AutoDukan = 3 -> Total = 5)
  it("Scenario 3: Both Return Products -> Separate tables with Total count 5", () => {
    const suppliers = [
      { id: "boodmo", vendor: "boodmo", name: "Boodmo", status: "Completed", productsExtracted: 2 },
      { id: "autodukan", vendor: "autodukan", name: "AutoDukan", status: "Completed", productsExtracted: 3 },
    ]
    const boodmoListings = [
      {
        title: "Boodmo Headlight RH",
        brand: "Lumax",
        part_number: "BM-HL-1",
        price: 3200,
        vendor: "boodmo",
        available: true,
        fitment: "verified",
        relevance: "EXACT_MATCH",
      },
      {
        title: "Boodmo Headlight LH",
        brand: "Lumax",
        part_number: "BM-HL-2",
        price: 3200,
        vendor: "boodmo",
        available: true,
        fitment: "verified",
        relevance: "EXACT_MATCH",
      },
    ]
    const autodukanListings = [
      {
        title: "AutoDukan Headlight RH",
        brand: "Uno Minda",
        part_number: "AD-HL-1",
        price: 3100,
        vendor: "autodukan",
        available: true,
        fitment: "verified",
        relevance: "EXACT_MATCH",
      },
      {
        title: "AutoDukan Headlight LH",
        brand: "Uno Minda",
        part_number: "AD-HL-2",
        price: 3100,
        vendor: "autodukan",
        available: true,
        fitment: "verified",
        relevance: "EXACT_MATCH",
      },
      {
        title: "AutoDukan Headlight Bulb H7",
        brand: "Osram",
        part_number: "AD-HL-3",
        price: 450,
        vendor: "autodukan",
        available: true,
        fitment: "verified",
        relevance: "COMPONENT",
      },
    ]

    render(
      <ProductResults
        boodmoListings={boodmoListings}
        autodukanListings={autodukanListings}
        suppliers={suppliers}
        activeFilter="all"
        onSelectFilter={vi.fn()}
      />
    )

    // Both tables populated
    expect(screen.getByText("Boodmo Headlight RH")).toBeDefined()
    expect(screen.getByText("Boodmo Headlight LH")).toBeDefined()
    expect(screen.getByText("AutoDukan Headlight RH")).toBeDefined()
    expect(screen.getByText("AutoDukan Headlight LH")).toBeDefined()
    expect(screen.getByText("AutoDukan Headlight Bulb H7")).toBeDefined()

    // Total count is 5
    expect(screen.getByText(/All Suppliers \(Separated\) \(5\)/i)).toBeDefined()
    expect(screen.getByText(/Boodmo Only \(2\)/i)).toBeDefined()
    expect(screen.getByText(/AutoDukan Only \(3\)/i)).toBeDefined()
  })

  // Test 4: Both Return 0 Products
  it("Scenario 4: Both Return 0 Products -> Independent empty states with Total 0", () => {
    const suppliers = [
      { id: "boodmo", vendor: "boodmo", name: "Boodmo", status: "No Results", productsExtracted: 0 },
      { id: "autodukan", vendor: "autodukan", name: "AutoDukan", status: "No Results", productsExtracted: 0 },
    ]

    render(
      <ProductResults
        boodmoListings={[]}
        autodukanListings={[]}
        suppliers={suppliers}
        activeFilter="all"
        onSelectFilter={vi.fn()}
      />
    )

    // Both sections show empty states
    const emptyMsgs = screen.getAllByText("0 products found.")
    expect(emptyMsgs.length).toBe(2)

    // Total is 0
    expect(screen.getByText(/All Suppliers \(Separated\) \(0\)/i)).toBeDefined()
    expect(screen.getByText(/Boodmo Only \(0\)/i)).toBeDefined()
    expect(screen.getByText(/AutoDukan Only \(0\)/i)).toBeDefined()
  })

  // Test 5: Out of Order Completion in App
  it("Scenario 5: Out of order completion -> Boodmo completes first in 5s with 2 products, AutoDukan later with 0", async () => {
    render(<App />)

    // 1. Simulate Boodmo completing at 5s with 2 products
    act(() => {
      eventCallback({
        type: "adapter_completed",
        supplier: "Boodmo",
        vendor: "boodmo",
        status: "Completed",
        products_found: 2,
        elapsed_seconds: 5.0,
        results: [
          {
            title: "Hyundai Creta Fast Headlamp RH",
            brand: "Hyundai",
            part_number: "92102-FAST1",
            price: 5200,
            vendor: "boodmo",
            available: true,
            fitment: "verified",
            relevance: "EXACT_MATCH",
          },
          {
            title: "Hyundai Creta Fast Headlamp LH",
            brand: "Hyundai",
            part_number: "92101-FAST2",
            price: 5200,
            vendor: "boodmo",
            available: true,
            fitment: "verified",
            relevance: "EXACT_MATCH",
          },
        ],
      })
    })

    // Boodmo's 2 products must appear immediately!
    expect(screen.getByText("Hyundai Creta Fast Headlamp RH")).toBeDefined()
    expect(screen.getByText("Hyundai Creta Fast Headlamp LH")).toBeDefined()
    expect(screen.getByText(/All Suppliers \(Separated\) \(2\)/i)).toBeDefined()

    // 2. Later at 15s AutoDukan finishes with 0 products
    act(() => {
      eventCallback({
        type: "adapter_completed",
        supplier: "AutoDukan",
        vendor: "autodukan",
        status: "No Results",
        products_found: 0,
        elapsed_seconds: 15.0,
        results: [],
      })
    })

    // Boodmo's 2 products must remain completely intact!
    expect(screen.getByText("Hyundai Creta Fast Headlamp RH")).toBeDefined()
    expect(screen.getByText("Hyundai Creta Fast Headlamp LH")).toBeDefined()
    expect(screen.getByText(/All Suppliers \(Separated\) \(2\)/i)).toBeDefined()
    expect(screen.getByText(/Boodmo Only \(2\)/i)).toBeDefined()
    expect(screen.getByText(/AutoDukan Only \(0\)/i)).toBeDefined()
  })

  // Test 6: One Adapter Fails while Boodmo succeeds
  it("Scenario 6: One Adapter Fails -> Boodmo products remain visible and total is 2", async () => {
    render(<App />)

    // Boodmo completes with 2 products
    act(() => {
      eventCallback({
        type: "adapter_completed",
        supplier: "Boodmo",
        vendor: "boodmo",
        status: "Completed",
        products_found: 2,
        elapsed_seconds: 4.2,
        results: [
          {
            title: "Creta Headlamp Unit RH",
            brand: "Hyundai OEM",
            part_number: "92102-M001",
            price: 4900,
            vendor: "boodmo",
            available: true,
            fitment: "verified",
            relevance: "EXACT_MATCH",
          },
          {
            title: "Creta Headlamp Unit LH",
            brand: "Hyundai OEM",
            part_number: "92101-M002",
            price: 4900,
            vendor: "boodmo",
            available: true,
            fitment: "verified",
            relevance: "EXACT_MATCH",
          },
        ],
      })
    })

    // AutoDukan fails
    act(() => {
      eventCallback({
        type: "adapter_failed",
        supplier: "AutoDukan",
        vendor: "autodukan",
        status: "Failed",
        error: "AutoDukan access blocked (403/429)",
        elapsed_seconds: 10.0,
      })
    })

    // Boodmo products must still be visible and count is 2
    expect(screen.getByText("Creta Headlamp Unit RH")).toBeDefined()
    expect(screen.getByText("Creta Headlamp Unit LH")).toBeDefined()
    expect(screen.getByText(/All Suppliers \(Separated\) \(2\)/i)).toBeDefined()
    expect(screen.getByText(/search could not complete/i)).toBeDefined()
  })

  // Test 7: Cache HIT Scenario
  it("Scenario 7: Cache HIT Scenario -> Repeat search populates both outcomes without losing products", async () => {
    render(<App />)

    // Simulate complete event delivering cached outcomes for Hyundai Creta headlights
    act(() => {
      eventCallback({
        type: "search_summary",
        summary: "For the Hyundai Creta 2026, Boodmo lists 2 parts under Headlight that fit this variant.",
        outcome: {
          status: "ok",
          is_cached: true,
          vendor_outcomes: [
            {
              vendor: "boodmo",
              supplier: "Boodmo",
              status: "ok",
              listings: [
                {
                  title: "Hyundai Creta LED Headlight RH (Cached)",
                  brand: "Hyundai",
                  part_number: "92102-CACHE1",
                  price: 5800,
                  vendor: "boodmo",
                  available: true,
                  fitment: "verified",
                  relevance: "EXACT_MATCH",
                },
                {
                  title: "Hyundai Creta LED Headlight LH (Cached)",
                  brand: "Hyundai",
                  part_number: "92101-CACHE2",
                  price: 5800,
                  vendor: "boodmo",
                  available: true,
                  fitment: "verified",
                  relevance: "EXACT_MATCH",
                },
              ],
            },
            {
              vendor: "autodukan",
              supplier: "AutoDukan",
              status: "no_results",
              listings: [],
            },
          ],
        },
      })
    })

    // Both outcomes rendered correctly from cache
    expect(screen.getByText("Hyundai Creta LED Headlight RH (Cached)")).toBeDefined()
    expect(screen.getByText("Hyundai Creta LED Headlight LH (Cached)")).toBeDefined()
    expect(screen.getByText(/All Suppliers \(Separated\) \(2\)/i)).toBeDefined()
    expect(screen.getByText(/Boodmo Only \(2\)/i)).toBeDefined()
    expect(screen.getByText(/AutoDukan Only \(0\)/i)).toBeDefined()
    expect(screen.getByText("0 products found.")).toBeDefined()
  })
})
