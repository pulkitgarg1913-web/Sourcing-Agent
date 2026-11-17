import React from "react"
import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import ProductResults from "../components/ProductResults"

describe("ProductResults Component", () => {
  const sampleSuppliers = [
    { id: "boodmo", vendor: "boodmo", name: "Boodmo", status: "Completed" },
    { id: "autodukan", vendor: "autodukan", name: "AutoDukan", status: "Completed" },
  ]

  const sampleListings = [
    {
      title: "Bosch Front Brake Pads",
      brand: "Bosch",
      part_number: "0986AB1234",
      price: 1200,
      mrp: 1600,
      url: "https://boodmo.com/p/1",
      vendor: "boodmo",
      available: true,
      fitment: "verified",
      relevance: "EXACT_MATCH",
      best_offer: { seller: "SparesHub", dispatch: "0-2 days", stock: "In Stock" },
    },
    {
      title: "Brembo High Performance Brake Pads",
      brand: "Brembo",
      part_number: "P83024",
      price: 2500,
      mrp: 3000,
      url: "https://boodmo.com/p/2",
      vendor: "boodmo",
      available: true,
      fitment: "verified",
      relevance: "EXACT_MATCH",
      best_offer: { seller: "Speedway Spares", dispatch: "Same day", stock: "In Stock" },
    },
    {
      title: "OEM Brake Pad Set",
      brand: "Maruti Genuine",
      part_number: "55810M68P00",
      price: 1450,
      mrp: 1550,
      url: "https://autodukan.com/p/3",
      vendor: "autodukan",
      available: true,
      fitment: "unverified",
      relevance: "EXACT_MATCH",
      best_offer: { seller: "AutoDukan Central", dispatch: "Standard", stock: "In Stock" },
    },
    {
      title: "Brake Caliper Guide Pin Kit",
      brand: "KBX",
      part_number: "KBX-99",
      price: 350,
      mrp: 400,
      url: "https://boodmo.com/p/4",
      vendor: "boodmo",
      available: true,
      fitment: "verified",
      relevance: "COMPONENT",
      best_offer: { seller: "BrakeTech", dispatch: "Standard", stock: "In Stock" },
    },
  ]

  it("renders both supplier sections separately without mixing listings", () => {
    render(
      <ProductResults
        listings={sampleListings}
        suppliers={sampleSuppliers}
        activeFilter="all"
        onSelectFilter={vi.fn()}
      />
    )

    expect(screen.getByText("Boodmo Verified Catalog")).toBeDefined()
    expect(screen.getByText("AutoDukan Verified Catalog")).toBeDefined()
    expect(screen.getByText("Bosch Front Brake Pads")).toBeDefined()
    expect(screen.getByText("OEM Brake Pad Set")).toBeDefined()
  })

  it("calculates exact procurement recommendation and savings", () => {
    render(
      <ProductResults
        listings={sampleListings}
        suppliers={sampleSuppliers}
        activeFilter="all"
        onSelectFilter={vi.fn()}
      />
    )

    // Boodmo cheapest is KBX Guide Pin Kit @ ₹350. AutoDukan is OEM @ ₹1,450.
    // Savings = 1,450 - 350 = ₹1,100.
    expect(screen.getByText(/Source from Boodmo: Save ₹1,100/i)).toBeDefined()
  })

  it("filters listings when search query is typed", () => {
    render(
      <ProductResults
        listings={sampleListings}
        suppliers={sampleSuppliers}
        activeFilter="all"
        onSelectFilter={vi.fn()}
      />
    )

    const searchInput = screen.getByPlaceholderText(/search parts, brands/i)
    fireEvent.change(searchInput, { target: { value: "Brembo" } })

    expect(screen.getByText("Brembo High Performance Brake Pads")).toBeDefined()
    expect(screen.queryByText("Bosch Front Brake Pads")).toBeNull()
  })

  it("filters listings by relevance category", () => {
    render(
      <ProductResults
        listings={sampleListings}
        suppliers={sampleSuppliers}
        activeFilter="all"
        onSelectFilter={vi.fn()}
      />
    )

    // Click on Exact Matches filter button
    const exactButton = screen.getByRole("button", { name: /Exact Matches \(3\)/i })
    fireEvent.click(exactButton)

    expect(screen.getByText("Bosch Front Brake Pads")).toBeDefined()
    expect(screen.queryByText("Brake Caliper Guide Pin Kit")).toBeNull()
  })

  it("renders both left and right side mirrors without deduplication collision", () => {
    const mirrorListings = [
      {
        title: "Outside Rear View Mirror Right Side With Indicator",
        brand: "Tata (OEM)",
        part_number: "542781100115",
        price: 1850,
        vendor: "boodmo",
        available: true,
        fitment: "verified",
        fitment_basis: "Boodmo fitment filter: Tata Nexon 2026 variant",
        relevance: "EXACT_MATCH",
        seller: "Authorized Supplier Hub",
        dispatch: "24–48 hours",
      },
      {
        title: "Outside Rear View Mirror Left Side With Indicator",
        brand: "Tata (OEM)",
        part_number: "542781100114",
        price: 1850,
        vendor: "boodmo",
        available: true,
        fitment: "verified",
        fitment_basis: "Boodmo fitment filter: Tata Nexon 2026 variant",
        relevance: "EXACT_MATCH",
        seller: "Authorized Supplier Hub",
        dispatch: "24–48 hours",
      },
    ]

    render(
      <ProductResults
        listings={mirrorListings}
        suppliers={sampleSuppliers}
        activeFilter="all"
        onSelectFilter={vi.fn()}
      />
    )

    expect(screen.getByText("Outside Rear View Mirror Right Side With Indicator")).toBeDefined()
    expect(screen.getByText("Outside Rear View Mirror Left Side With Indicator")).toBeDefined()
    expect(screen.getByText("542781100115")).toBeDefined()
    expect(screen.getByText("542781100114")).toBeDefined()
  })

  it("renders AutoDukan products with identical search page URLs without dropping items", () => {
    const autoDukanListings = [
      {
        title: "Tata Nexon Side Door Mirror Assembly RH",
        brand: "Tata Motors",
        part_number: "542781100115",
        price: 1799,
        url: "https://autodukan.com/products-list?search=Tata+Nexon+Side+Mirror",
        vendor: "autodukan",
        available: true,
        fitment: "partial",
        fitment_basis: "Verified for Tata Nexon 2024 catalog; verify 2026 compatibility before purchase",
        relevance: "EXACT_MATCH",
        seller: "AutoDukan Distributor",
        dispatch: "24–48 hours",
      },
      {
        title: "Tata Nexon Mirror Glass Plate with Base RH",
        brand: "Lumax",
        part_number: "LX-MG-8812",
        price: 420,
        url: "https://autodukan.com/products-list?search=Tata+Nexon+Side+Mirror",
        vendor: "autodukan",
        available: true,
        fitment: "partial",
        fitment_basis: "Verified for Tata Nexon 2024 catalog; verify 2026 compatibility before purchase",
        relevance: "COMPONENT",
        seller: "AutoDukan Distributor",
        dispatch: "24–48 hours",
      },
    ]

    render(
      <ProductResults
        listings={autoDukanListings}
        suppliers={sampleSuppliers}
        activeFilter="all"
        onSelectFilter={vi.fn()}
      />
    )

    expect(screen.getByText("Tata Nexon Side Door Mirror Assembly RH")).toBeDefined()
    expect(screen.getByText("Tata Nexon Mirror Glass Plate with Base RH")).toBeDefined()
    expect(screen.getByText("LX-MG-8812")).toBeDefined()
    expect(screen.getAllByText(/Verified for Tata Nexon 2024 catalog/i).length).toBe(2)
  })

  it("renders graceful 'Not provided' fallback for missing optional fields", () => {
    const sparseListing = [
      {
        title: "Generic Brake Hose",
        brand: null,
        part_number: null,
        price: null,
        url: null,
        vendor: "boodmo",
        available: null,
        fitment: "unverified",
        fitment_basis: null,
        seller: null,
        dispatch: null,
        relevance: "NEEDS_REVIEW",
      },
    ]

    render(
      <ProductResults
        listings={sparseListing}
        suppliers={sampleSuppliers}
        activeFilter="all"
        onSelectFilter={vi.fn()}
      />
    )

    expect(screen.getByText("Generic Brake Hose")).toBeDefined()
    expect(screen.getAllByText("Not provided").length).toBeGreaterThanOrEqual(4)
    expect(screen.getByText("Unknown")).toBeDefined()
    expect(screen.getByText("Unverified")).toBeDefined()
  })

  it("preserves zero product loss across narrative scenario products", () => {
    const scenarioListings = [
      {
        title: "Tata (OEM) Outside Rear View Mirror Right Side With Indicator",
        brand: "Tata (OEM)",
        part_number: "542781100115",
        price: 1850,
        vendor: "boodmo",
        available: true,
        fitment: "verified",
        fitment_basis: "Boodmo fitment filter: Tata Nexon 2026 variant",
        relevance: "EXACT_MATCH",
        seller: "Authorized Supplier Hub",
        dispatch: "24–48 hours",
      },
      {
        title: "Tata (OEM) Outside Rear View Mirror Left Side With Indicator",
        brand: "Tata (OEM)",
        part_number: "542781100114",
        price: 1850,
        vendor: "boodmo",
        available: true,
        fitment: "verified",
        fitment_basis: "Boodmo fitment filter: Tata Nexon 2026 variant",
        relevance: "EXACT_MATCH",
        seller: "Authorized Supplier Hub",
        dispatch: "24–48 hours",
      },
      {
        title: "Lumax Tata Nexon Mirror Glass Plate with Base RH",
        brand: "Lumax",
        part_number: "LX-MG-8812",
        price: 420,
        vendor: "autodukan",
        available: true,
        fitment: "partial",
        fitment_basis: "Verified for Tata Nexon 2024 catalog; verify 2026 compatibility before purchase",
        relevance: "COMPONENT",
        seller: "AutoDukan Distributor",
        dispatch: "24–48 hours",
      },
    ]

    render(
      <ProductResults
        listings={scenarioListings}
        suppliers={sampleSuppliers}
        activeFilter="all"
        onSelectFilter={vi.fn()}
      />
    )

    // All 3 scenario products must be rendered
    expect(screen.getByText("Tata (OEM) Outside Rear View Mirror Right Side With Indicator")).toBeDefined()
    expect(screen.getByText("Tata (OEM) Outside Rear View Mirror Left Side With Indicator")).toBeDefined()
    expect(screen.getByText("Lumax Tata Nexon Mirror Glass Plate with Base RH")).toBeDefined()
  })
})
