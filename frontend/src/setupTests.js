// Setup file for Vitest / JSDOM environment
if (typeof window !== "undefined") {
  window.HTMLElement.prototype.scrollIntoView = () => {}
  window.HTMLElement.prototype.scrollTo = () => {}
}
