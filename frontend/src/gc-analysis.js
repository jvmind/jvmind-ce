// Backward-compatible shell: the GC analysis module was split into ./gc-analysis/*.
// Importing this file preserves the original module path for main.js, report.js
// and tests, and triggers the DOM event bindings via the package index.
export * from "./gc-analysis/index.js";
