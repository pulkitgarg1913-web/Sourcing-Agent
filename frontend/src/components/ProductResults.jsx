import React, { useState, useMemo } from "react"
import {
  ExternalLink,
  ShieldCheck,
  ShieldAlert,
  ArrowUpDown,
  Filter,
  CheckCircle2,
  XCircle,
  HelpCircle,
  Package,
  Layers,
  Search,
  ChevronDown,
  ChevronUp,
  Sparkles,
  Info,
  Wrench,
  Tag,
  Truck,
  Columns,
  Rows,
} from "lucide-react"
import { formatRupees } from "../lib/utils"

export default function ProductResults({
  listings = [],
  suppliers = [],
  activeFilter = "all",
  onSelectFilter,
}) {
  const [relevanceFilter, setRelevanceFilter] = useState("all") // all | exact | close | component | accessory
  const [stockFilter, setStockFilter] = useState("all") // all | in_stock | out_of_stock
  const [sortOption, setSortOption] = useState("price_asc") // price_asc | price_desc | best_match | brand | fitment
  const [searchQuery, setSearchQuery] = useState("")
  const [showAccessoriesMap, setShowAccessoriesMap] = useState({}) // { boodmo: bool, autodukan: bool }
  const [layoutMode, setLayoutMode] = useState("stacked") // stacked | side_by_side

  // Filter listings by relevance, stock & search text
  const filterListings = (items) => {
    return items.filter((item) => {
      if (item.relevance === "IRRELEVANT") return false

      const rel = item.relevance || "EXACT_MATCH"
      if (relevanceFilter === "exact" && rel !== "EXACT_MATCH") return false
      if (relevanceFilter === "close" && rel !== "CLOSE_MATCH") return false
      if (relevanceFilter === "component" && rel !== "COMPONENT") return false
      if (relevanceFilter === "accessory" && rel !== "ACCESSORY") return false

      if (stockFilter === "in_stock" && item.available !== true) return false
      if (stockFilter === "out_of_stock" && item.available !== false) return false

      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase()
        const title = (item.title || "").toLowerCase()
        const brand = (item.brand || "").toLowerCase()
        const partNo = (item.part_number || "").toLowerCase()
        if (!title.includes(q) && !brand.includes(q) && !partNo.includes(q)) {
          return false
        }
      }
      return true
    })
  }

  // Sort helper preserving relevance tiers
  const sortListings = (items) => {
    const list = [...items]
    const tierOrder = {
      EXACT_MATCH: 1,
      CLOSE_MATCH: 2,
      COMPONENT: 3,
      ACCESSORY: 4,
      NEEDS_REVIEW: 5,
    }

    list.sort((a, b) => {
      const tierA = tierOrder[a.relevance] || 5
      const tierB = tierOrder[b.relevance] || 5

      if (sortOption === "best_match") {
        if (tierA !== tierB) return tierA - tierB
        const pa = a.price ?? Infinity
        const pb = b.price ?? Infinity
        return pa - pb
      } else if (sortOption === "price_asc") {
        if (tierA !== tierB) return tierA - tierB
        const pa = a.price ?? Infinity
        const pb = b.price ?? Infinity
        return pa - pb
      } else if (sortOption === "price_desc") {
        if (tierA !== tierB) return tierA - tierB
        const pa = a.price ?? -Infinity
        const pb = b.price ?? -Infinity
        return pb - pa
      } else if (sortOption === "brand") {
        return (a.brand || "").localeCompare(b.brand || "")
      } else if (sortOption === "fitment") {
        const fitOrder = { verified: 1, partial: 2, unverified: 3 }
        return (fitOrder[a.fitment] || 4) - (fitOrder[b.fitment] || 4)
      }
      return 0
    })
    return list
  }

  const isBoodmo = (l) => {
    const v = (l.vendor || l.supplier || l.supplier_name || "").toLowerCase()
    return v.includes("boodmo") || (l.url && l.url.toLowerCase().includes("boodmo"))
  }
  const isAutoDukan = (l) => {
    const v = (l.vendor || l.supplier || l.supplier_name || "").toLowerCase()
    return v.includes("autodukan") || (l.url && l.url.toLowerCase().includes("autodukan"))
  }

  // Separate listings by adapter with robust supplier fallback
  const boodmoRaw = useMemo(
    () => listings.filter(isBoodmo),
    [listings]
  )
  const autodukanRaw = useMemo(
    () => listings.filter(isAutoDukan),
    [listings]
  )

  const boodmoFiltered = useMemo(
    () => sortListings(filterListings(boodmoRaw)),
    [boodmoRaw, relevanceFilter, stockFilter, searchQuery, sortOption]
  )
  const autodukanFiltered = useMemo(
    () => sortListings(filterListings(autodukanRaw)),
    [autodukanRaw, relevanceFilter, stockFilter, searchQuery, sortOption]
  )

  // Supplier counts
  const supplierCounts = useMemo(() => {
    const valid = listings.filter((l) => l.relevance !== "IRRELEVANT")
    return {
      all: valid.length,
      boodmo: valid.filter(isBoodmo).length,
      autodukan: valid.filter(isAutoDukan).length,
    }
  }, [listings])

  // Stock counts
  const stockCounts = useMemo(() => {
    const valid = listings.filter((l) => l.relevance !== "IRRELEVANT")
    return {
      all: valid.length,
      inStock: valid.filter((l) => l.available === true).length,
      outOfStock: valid.filter((l) => l.available === false).length,
    }
  }, [listings])

  // Relevance counts
  const relevanceCounts = useMemo(() => {
    const valid = listings.filter((l) => l.relevance !== "IRRELEVANT")
    const counts = { all: valid.length, exact: 0, close: 0, component: 0, accessory: 0 }
    for (const l of valid) {
      const r = l.relevance || "EXACT_MATCH"
      if (r === "EXACT_MATCH") counts.exact++
      else if (r === "CLOSE_MATCH") counts.close++
      else if (r === "COMPONENT") counts.component++
      else if (r === "ACCESSORY") counts.accessory++
    }
    return counts
  }, [listings])

  // Helper to render a table row
  const renderTableRow = (item, idx) => {
    const bestOffer = item.best_offer || {}
    const hasOffer = Boolean(bestOffer.seller || item.offers?.length > 0 || item.seller)
    const isPriceVerified = hasOffer && item.price !== null && item.price !== undefined
    const supplierName = item.supplier || (item.vendor === "autodukan" ? "AutoDukan" : "Boodmo")
    const seller = item.seller || bestOffer.seller || "Not provided"
    const dispatch = item.dispatch || bestOffer.dispatch || "Not provided"

    const relevanceBadgeConfig = {
      EXACT_MATCH: {
        label: "Exact Match",
        className: "bg-emerald-50 text-emerald-800 border-emerald-200 font-semibold",
        icon: ShieldCheck,
      },
      CLOSE_MATCH: {
        label: "Close Match",
        className: "bg-teal-50 text-teal-800 border-teal-200 font-medium",
        icon: CheckCircle2,
      },
      COMPONENT: {
        label: "Component",
        className: "bg-indigo-50 text-indigo-800 border-indigo-200 font-medium",
        icon: Wrench,
      },
      ACCESSORY: {
        label: "Accessory",
        className: "bg-amber-50 text-amber-800 border-amber-200 font-medium",
        icon: Tag,
      },
      NEEDS_REVIEW: {
        label: "Needs Review",
        className: "bg-slate-100 text-slate-700 border-slate-200 font-medium",
        icon: HelpCircle,
      },
    }

    const relConfig =
      relevanceBadgeConfig[item.relevance] || relevanceBadgeConfig.NEEDS_REVIEW
    const RelIcon = relConfig.icon

    return (
      <tr key={idx} className="hover:bg-slate-50/80 transition-colors group">
        {/* 1. Title, Supplier & Relevance */}
        <td className="py-2.5 px-3 max-w-xs">
          <div className="font-semibold text-slate-900 group-hover:text-blue-600 transition-colors line-clamp-2 text-xs">
            {item.title || "Not provided"}
          </div>

          <div className="flex flex-wrap items-center gap-1 mt-1">
            <span
              className={`inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border ${
                item.vendor === "autodukan"
                  ? "bg-orange-50 text-orange-700 border-orange-200"
                  : "bg-red-50 text-red-700 border-red-200"
              } font-semibold`}
            >
              {supplierName}
            </span>

            <span
              className={`inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border ${relConfig.className}`}
              title={item.relevance_reason || relConfig.label}
            >
              <RelIcon className="w-2.5 h-2.5" />
              <span>{relConfig.label}</span>
            </span>

            {/* Complete Assembly badge */}
            {(item.title?.toLowerCase().includes("assembly") ||
              item.assembly_type === "complete" ||
              item.product_type === "assembly") && (
              <span className="text-[10px] font-semibold text-indigo-700 bg-indigo-50 border border-indigo-200 px-1.5 py-0.5 rounded">
                Complete Assembly
              </span>
            )}

            {/* Component badge */}
            {(item.relevance === "COMPONENT" || item.product_type === "component") && (
              <span className="text-[10px] font-medium text-purple-700 bg-purple-50 border border-purple-200 px-1.5 py-0.5 rounded">
                Component
              </span>
            )}

            {item.product_type && item.product_type !== "assembly" && item.product_type !== "component" && (
              <span className="text-[10px] font-medium text-slate-500 bg-slate-100 px-1.5 py-0.5 rounded capitalize">
                {item.product_type.replace("_", " ")}
              </span>
            )}

            {item.origin && (
              <span className="text-[10px] font-medium text-slate-500 bg-slate-100 px-1.5 py-0.5 rounded">
                {item.origin}
              </span>
            )}
          </div>
        </td>

        {/* 2. Verified Price */}
        <td className="py-2.5 px-3 whitespace-nowrap">
          {item.price !== null && item.price !== undefined ? (
            <div>
              <div className="font-bold text-slate-900 text-xs">
                {formatRupees(item.price)}
              </div>
              {isPriceVerified ? (
                <span className="inline-flex items-center gap-0.5 text-[9px] font-semibold text-emerald-700 bg-emerald-50 px-1.5 py-0.2 rounded border border-emerald-200">
                  <ShieldCheck className="w-2.5 h-2.5" />
                  Verified
                </span>
              ) : (
                <span className="text-[9px] text-slate-500 bg-slate-100 px-1.5 py-0.2 rounded">
                  Listing Price
                </span>
              )}
              {item.mrp && item.mrp > item.price && (
                <div className="text-[9px] text-slate-400 line-through">
                  MRP {formatRupees(item.mrp)}
                </div>
              )}
            </div>
          ) : (
            <span className="text-slate-400 italic text-[11px]">Not provided</span>
          )}
        </td>

        {/* 3. Seller & Dispatch */}
        <td className="py-2.5 px-3 max-w-[140px]">
          <div>
            <span
              className="font-medium text-slate-800 block truncate text-xs"
              title={seller}
            >
              {seller}
            </span>
            <span className="text-[10px] text-slate-500 block truncate" title={dispatch}>
              {dispatch}
            </span>
          </div>
        </td>

        {/* 4. Stock Availability */}
        <td className="py-2.5 px-3 whitespace-nowrap">
          {item.available === true ? (
            <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded-md border border-emerald-200">
              <CheckCircle2 className="w-3 h-3" />
              <span>{bestOffer.stock || "In stock"}</span>
            </span>
          ) : item.available === false ? (
            <span className="inline-flex items-center gap-1 text-[10px] font-medium text-rose-700 bg-rose-50 px-2 py-0.5 rounded-md border border-rose-200">
              <XCircle className="w-3 h-3" />
              <span>Out of stock</span>
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 text-[10px] font-medium text-slate-600 bg-slate-100 px-2 py-0.5 rounded-md border border-slate-200">
              <HelpCircle className="w-3 h-3 text-slate-400" />
              <span>Unknown</span>
            </span>
          )}
        </td>

        {/* 5. Part Number */}
        <td className="py-2.5 px-3 whitespace-nowrap font-mono text-[11px] text-slate-700">
          {item.part_number ? (
            <div>
              <span>{item.part_number}</span>
              {item.part_number.includes("...") && (
                <span className="block text-[8px] text-amber-600">
                  (Masked)
                </span>
              )}
            </div>
          ) : (
            <span className="text-slate-400 italic text-[11px]">Not provided</span>
          )}
        </td>

        {/* 6. Brand */}
        <td className="py-2.5 px-3 whitespace-nowrap font-medium text-slate-800 text-xs">
          {item.brand || "Not provided"}
        </td>

        {/* 7. Fitment & Fitment Basis */}
        <td className="py-2.5 px-3 min-w-[150px] max-w-[220px]">
          <div>
            {item.fitment === "verified" ? (
              <span
                className="inline-flex items-center gap-1 text-[10px] font-semibold text-emerald-700 bg-emerald-50 px-1.5 py-0.5 rounded-md border border-emerald-200"
                title={item.fitment_basis || "Verified via catalog filter"}
              >
                <ShieldCheck className="w-2.5 h-2.5" />
                <span>Verified</span>
              </span>
            ) : item.fitment === "partial" ? (
              <span
                className="inline-flex items-center gap-1 text-[10px] font-medium text-amber-700 bg-amber-50 px-1.5 py-0.5 rounded-md border border-amber-200"
                title={item.fitment_basis || "Partial variant catalog match"}
              >
                <ShieldAlert className="w-2.5 h-2.5" />
                <span>Partial</span>
              </span>
            ) : (
              <span
                className="inline-flex items-center gap-1 text-[10px] font-medium text-slate-600 bg-slate-100 px-1.5 py-0.5 rounded-md border border-slate-200"
                title={item.fitment_basis || "Confirm compatibility"}
              >
                <HelpCircle className="w-2.5 h-2.5 text-slate-400" />
                <span>Unverified</span>
              </span>
            )}
            <div
              className="text-[10px] text-slate-500 mt-0.5 leading-tight line-clamp-2"
              title={item.fitment_basis || "Not provided"}
            >
              {item.fitment_basis || "Not provided"}
            </div>
          </div>
        </td>

        {/* 8. Action Link */}
        <td className="py-2.5 px-3 text-right whitespace-nowrap">
          {item.url ? (
            <a
              href={item.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-semibold text-blue-600 hover:text-blue-800 bg-blue-50/70 hover:bg-blue-100 rounded border border-blue-200 transition-colors shadow-2xs"
            >
              <span>View</span>
              <ExternalLink className="w-2.5 h-2.5" />
            </a>
          ) : (
            <span className="text-slate-400 italic text-[11px]">Not provided</span>
          )}
        </td>
      </tr>
    )
  }

  // Sub-component: A clean, dedicated table for a single supplier adapter
  const SupplierTableSection = ({ vendor, title, badgeColor, items, supplierInfo }) => {
    const isCompleted = supplierInfo?.status === "Completed"
    const isFailed = supplierInfo?.status === "Failed"
    const isBlocked = supplierInfo?.status === "Blocked"
    const isSearching =
      !isCompleted &&
      !isFailed &&
      !isBlocked &&
      supplierInfo?.status !== "Waiting" &&
      supplierInfo?.status !== "Pending" &&
      supplierInfo?.status !== "Idle"

    const inStockCount = items.filter((x) => x.available === true).length
    const cheapest = items
      .filter((x) => x.available === true && x.price !== null)
      .reduce(
        (min, curr) => (curr.price < (min?.price ?? Infinity) ? curr : min),
        null
      )

    return (
      <div id={`supplier-section-${vendor}`} className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden flex flex-col">
        {/* Adapter Branded Header */}
        <div className="px-4 py-3 bg-slate-50/80 border-b border-slate-200 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className={`px-2.5 py-0.5 rounded-md text-xs font-bold border ${badgeColor}`}>
              {title}
            </span>
            <span className="text-xs text-slate-500 font-medium">
              {items.length} parts found
            </span>
            {inStockCount > 0 && (
              <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded-full border border-emerald-200">
                <CheckCircle2 className="w-3 h-3" />
                {inStockCount} in stock
              </span>
            )}
            {cheapest && (
              <span className="text-[11px] font-bold text-slate-900 bg-white px-2 py-0.5 rounded-md border border-slate-200 shadow-2xs">
                Cheapest: {formatRupees(cheapest.price)}
              </span>
            )}
          </div>

          <div className="flex items-center gap-2">
            {isCompleted && (
              <span className="text-[10px] font-semibold text-emerald-700 bg-emerald-50 border border-emerald-200 px-2 py-0.5 rounded">
                Extraction Complete
              </span>
            )}
            {isSearching && (
              <span className="text-[10px] font-semibold text-blue-700 bg-blue-50 border border-blue-200 px-2 py-0.5 rounded animate-pulse">
                {supplierInfo?.status || "Searching..."}
              </span>
            )}
            {isFailed && (
              <span className="text-[10px] font-semibold text-rose-700 bg-rose-50 border border-rose-200 px-2 py-0.5 rounded">
                Search Failed
              </span>
            )}
            {isBlocked && (
              <span className="text-[10px] font-semibold text-amber-700 bg-amber-50 border border-amber-200 px-2 py-0.5 rounded">
                Blocked
              </span>
            )}
            {(supplierInfo?.status === "Waiting" || supplierInfo?.status === "Pending" || !supplierInfo?.status) && !isCompleted && !isFailed && !isBlocked && (
              <span className="text-[10px] font-medium text-slate-500 bg-slate-100 border border-slate-200 px-2 py-0.5 rounded">
                Pending
              </span>
            )}
          </div>
        </div>

        {/* Content Body */}
        {isFailed ? (
          <div className="p-6 text-center bg-rose-50/30 text-rose-700 text-xs">
            <ShieldAlert className="w-6 h-6 mx-auto mb-1 text-rose-500" />
            <p className="font-semibold">{title} search could not complete</p>
            <p className="text-slate-500 mt-0.5">
              {supplierInfo?.errorMessage || "Vehicle or category not matched on site."}
            </p>
          </div>
        ) : items.length === 0 ? (
          <div className="p-8 text-center text-slate-400 text-xs">
            <Package className="w-8 h-8 mx-auto mb-1.5 text-slate-300" />
            <p className="font-semibold text-slate-600">
              {isSearching
                ? `${title} is currently searching catalogs...`
                : isCompleted
                ? `${title} has no matching verified inventory in stock for this car.`
                : `Ready to search ${title}`}
            </p>
            <p className="text-[11px] text-slate-400 mt-0.5">
              {isSearching
                ? "Results will appear here as soon as extraction finishes."
                : isCompleted
                ? "No parts met the verified modification fitment criteria."
                : "Type a part name or vehicle details in the chat above."}
            </p>
          </div>
        ) : (
          <div className="flex-1 flex flex-col">
            {/* Dedicated Table with All Supplier Products */}
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-xs">
                <thead>
                  <tr className="bg-slate-50 text-slate-600 border-b border-slate-200 font-semibold uppercase tracking-wider text-[10px]">
                    <th className="py-2.5 px-3">Part Title &amp; Match</th>
                    <th className="py-2.5 px-3">Price</th>
                    <th className="py-2.5 px-3">Seller / Dispatch</th>
                    <th className="py-2.5 px-3">Stock</th>
                    <th className="py-2.5 px-3">Part No.</th>
                    <th className="py-2.5 px-3">Brand</th>
                    <th className="py-2.5 px-3 min-w-[150px]">Fitment &amp; Basis</th>
                    <th className="py-2.5 px-3 text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 font-normal">
                  {items.map((item, idx) => renderTableRow(item, idx))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    )
  }

  const boodmoSupplier = suppliers.find((s) => s.vendor === "boodmo")
  const autodukanSupplier = suppliers.find((s) => s.vendor === "autodukan")

  return (
    <div id="product-results-section" className="space-y-4">
      {/* Global Controls Bar: Supplier Selection, View Mode, Filters & Search */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-3.5 sm:p-4 space-y-3">
        <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-3 pb-3 border-b border-slate-100">
          {/* Supplier Navigation Tabs */}
          <div className="flex items-center gap-1.5 overflow-x-auto pb-1 lg:pb-0">
            <span className="text-xs font-bold text-slate-600 mr-1 shrink-0">
              Supplier View:
            </span>
            <button
              type="button"
              onClick={() => onSelectFilter("all")}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all shrink-0 cursor-pointer ${
                activeFilter === "all"
                  ? "bg-blue-600 text-white shadow-sm"
                  : "bg-slate-100 hover:bg-slate-200 text-slate-700"
              }`}
            >
              All Suppliers (Separated) ({supplierCounts.all})
            </button>
            <button
              type="button"
              onClick={() => onSelectFilter("boodmo")}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all shrink-0 cursor-pointer ${
                activeFilter === "boodmo"
                  ? "bg-red-600 text-white shadow-sm"
                  : "bg-red-50 hover:bg-red-100 text-red-700 border border-red-200"
              }`}
            >
              Boodmo Only ({supplierCounts.boodmo})
            </button>
            <button
              type="button"
              onClick={() => onSelectFilter("autodukan")}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all shrink-0 cursor-pointer ${
                activeFilter === "autodukan"
                  ? "bg-orange-600 text-white shadow-sm"
                  : "bg-orange-50 hover:bg-orange-100 text-orange-700 border border-orange-200"
              }`}
            >
              AutoDukan Only ({supplierCounts.autodukan})
            </button>
          </div>

          {/* Right Controls: Layout Mode, Sort, Search */}
          <div className="flex flex-wrap items-center gap-2">
            {/* Layout Toggle (Only when All Suppliers is active) */}
            {activeFilter === "all" && (
              <div className="flex items-center bg-slate-100 p-0.5 rounded-lg border border-slate-200">
                <button
                  type="button"
                  onClick={() => setLayoutMode("stacked")}
                  title="Stacked Dedicated Sections"
                  className={`p-1.5 rounded-md transition-colors cursor-pointer ${
                    layoutMode === "stacked"
                      ? "bg-white text-blue-600 shadow-2xs font-semibold"
                      : "text-slate-500 hover:text-slate-800"
                  }`}
                >
                  <Rows className="w-3.5 h-3.5" />
                </button>
                <button
                  type="button"
                  onClick={() => setLayoutMode("side_by_side")}
                  title="Side-by-Side Split View"
                  className={`p-1.5 rounded-md transition-colors cursor-pointer ${
                    layoutMode === "side_by_side"
                      ? "bg-white text-blue-600 shadow-2xs font-semibold"
                      : "text-slate-500 hover:text-slate-800"
                  }`}
                >
                  <Columns className="w-3.5 h-3.5" />
                </button>
              </div>
            )}

            {/* Sort Selector */}
            <div className="relative">
              <select
                value={sortOption}
                onChange={(e) => setSortOption(e.target.value)}
                className="text-xs font-medium bg-white border border-slate-200 rounded-lg px-2.5 py-1.5 pr-7 text-slate-700 hover:border-slate-300 focus:outline-none focus:ring-1 focus:ring-blue-500 cursor-pointer shadow-2xs"
              >
                <option value="best_match">Best Match</option>
                <option value="price_asc">Price: Low to High</option>
                <option value="price_desc">Price: High to Low</option>
                <option value="fitment">Fitment: Verified First</option>
                <option value="brand">Brand: A to Z</option>
              </select>
              <ArrowUpDown className="w-3 h-3 text-slate-400 absolute right-2 top-2.5 pointer-events-none" />
            </div>

            {/* Quick Search */}
            <div className="relative">
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search parts, brands..."
                className="text-xs bg-white border border-slate-200 rounded-lg pl-7 pr-3 py-1.5 focus:outline-none focus:ring-1 focus:ring-blue-500 shadow-2xs w-36 sm:w-44"
              >
              </input>
              <Search className="w-3.5 h-3.5 text-slate-400 absolute left-2 top-2 pointer-events-none" />
            </div>
          </div>
        </div>

        {/* Filter Rows: Match Tiers & Stock Availability */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2.5 pt-0.5 border-t border-slate-100">
          {/* Match Tiers Filter */}
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-xs font-semibold text-slate-500 flex items-center gap-1 mr-1">
              <Filter className="w-3 h-3 text-slate-400" />
              Match:
            </span>
            <button
              type="button"
              onClick={() => setRelevanceFilter("all")}
              className={`text-xs px-2.5 py-1 rounded-md transition-colors cursor-pointer ${
                relevanceFilter === "all"
                  ? "bg-slate-800 text-white font-semibold"
                  : "bg-slate-100 hover:bg-slate-200 text-slate-700"
              }`}
            >
              All Matches ({relevanceCounts.all})
            </button>
            {relevanceCounts.exact > 0 && (
              <button
                type="button"
                onClick={() => setRelevanceFilter("exact")}
                className={`text-xs px-2.5 py-1 rounded-md transition-colors cursor-pointer ${
                  relevanceFilter === "exact"
                    ? "bg-emerald-600 text-white font-semibold"
                    : "bg-emerald-50 hover:bg-emerald-100 text-emerald-800 border border-emerald-200"
                }`}
              >
                Exact Matches ({relevanceCounts.exact})
              </button>
            )}
            {relevanceCounts.close > 0 && (
              <button
                type="button"
                onClick={() => setRelevanceFilter("close")}
                className={`text-xs px-2.5 py-1 rounded-md transition-colors cursor-pointer ${
                  relevanceFilter === "close"
                    ? "bg-teal-600 text-white font-semibold"
                    : "bg-teal-50 hover:bg-teal-100 text-teal-800 border border-teal-200"
                }`}
              >
                Similar Matches ({relevanceCounts.close})
              </button>
            )}
            {relevanceCounts.component > 0 && (
              <button
                type="button"
                onClick={() => setRelevanceFilter("component")}
                className={`text-xs px-2.5 py-1 rounded-md transition-colors cursor-pointer ${
                  relevanceFilter === "component"
                    ? "bg-indigo-600 text-white font-semibold"
                    : "bg-indigo-50 hover:bg-indigo-100 text-indigo-800 border border-indigo-200"
                }`}
              >
                Components ({relevanceCounts.component})
              </button>
            )}
            {relevanceCounts.accessory > 0 && (
              <button
                type="button"
                onClick={() => setRelevanceFilter("accessory")}
                className={`text-xs px-2.5 py-1 rounded-md transition-colors cursor-pointer ${
                  relevanceFilter === "accessory"
                    ? "bg-amber-600 text-white font-semibold"
                    : "bg-amber-50 hover:bg-amber-100 text-amber-800 border border-amber-200"
                }`}
              >
                Accessories ({relevanceCounts.accessory})
              </button>
            )}
          </div>

          {/* Stock Availability Filter */}
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-xs font-semibold text-slate-500 mr-1">
              Stock:
            </span>
            <button
              type="button"
              onClick={() => setStockFilter("all")}
              className={`text-xs px-2.5 py-1 rounded-md transition-colors cursor-pointer ${
                stockFilter === "all"
                  ? "bg-slate-800 text-white font-semibold"
                  : "bg-slate-100 hover:bg-slate-200 text-slate-700"
              }`}
            >
              All Stock ({stockCounts.all})
            </button>
            <button
              type="button"
              onClick={() => setStockFilter("in_stock")}
              className={`text-xs px-2.5 py-1 rounded-md transition-colors cursor-pointer ${
                stockFilter === "in_stock"
                  ? "bg-emerald-600 text-white font-semibold"
                  : "bg-emerald-50 hover:bg-emerald-100 text-emerald-800 border border-emerald-200"
              }`}
            >
              In Stock ({stockCounts.inStock})
            </button>
            {stockCounts.outOfStock > 0 && (
              <button
                type="button"
                onClick={() => setStockFilter("out_of_stock")}
                className={`text-xs px-2.5 py-1 rounded-md transition-colors cursor-pointer ${
                  stockFilter === "out_of_stock"
                    ? "bg-rose-600 text-white font-semibold"
                    : "bg-rose-50 hover:bg-rose-100 text-rose-800 border border-rose-200"
                }`}
              >
                Out of Stock ({stockCounts.outOfStock})
              </button>
            )}
          </div>
        </div>
      </div>

      {/* 3. Dedicated Supplier Product Tables */}
      {activeFilter === "all" ? (
        <div
          className={
            layoutMode === "side_by_side"
              ? "grid grid-cols-1 xl:grid-cols-2 gap-4 items-start"
              : "space-y-4"
          }
        >
          {/* Boodmo Dedicated Section */}
          <SupplierTableSection
            vendor="boodmo"
            title="Boodmo Verified Catalog"
            badgeColor="bg-red-50 text-red-700 border-red-200"
            items={boodmoFiltered}
            supplierInfo={boodmoSupplier}
          />

          {/* AutoDukan Dedicated Section */}
          <SupplierTableSection
            vendor="autodukan"
            title="AutoDukan Verified Catalog"
            badgeColor="bg-orange-50 text-orange-700 border-orange-200"
            items={autodukanFiltered}
            supplierInfo={autodukanSupplier}
          />
        </div>
      ) : activeFilter === "boodmo" ? (
        <SupplierTableSection
          vendor="boodmo"
          title="Boodmo Verified Catalog"
          badgeColor="bg-red-50 text-red-700 border-red-200"
          items={boodmoFiltered}
          supplierInfo={boodmoSupplier}
        />
      ) : (
        <SupplierTableSection
          vendor="autodukan"
          title="AutoDukan Verified Catalog"
          badgeColor="bg-orange-50 text-orange-700 border-orange-200"
          items={autodukanFiltered}
          supplierInfo={autodukanSupplier}
        />
      )}
    </div>
  )
}
