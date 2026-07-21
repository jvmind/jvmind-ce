import { app } from "../app.js";
import {
  reportScopeUrl,
  addActiveReportContext,
  removeActiveReportContext,
  clearActiveReportContext,
  removeActiveReportContextByReport,
  bindReportBulkActions,
  deleteReportEntries,
  renderActiveReportContext,
  bindReportContext,
} from "./context.js";
import {
  openReport,
  activateReportTab,
  closeReportTab,
  renderReportTabs,
} from "./tabs.js";
import { analysisOpen, analysisClose } from "./panel.js";
import { appendSystemHint, renderReport, saveToReport } from "./render.js";
import {
  refreshHistory,
  updateBadge,
  refreshAllReportHistory,
  renderAllReportHistory,
} from "./history.js";
import {
  enableGcSidebar,
  renderGcSidebar,
  updateReportHeader,
  toggleGcSidebar,
} from "./sidebar.js";
// Side-effect: wire DOM event handlers (FAB, close, tabs, upload zone).
import "./bindings.js";

// Re-export the full public surface (consumed by report.js, tests, main.js).
export {
  reportScopeUrl,
  addActiveReportContext,
  removeActiveReportContext,
  clearActiveReportContext,
  removeActiveReportContextByReport,
  bindReportBulkActions,
  deleteReportEntries,
  renderActiveReportContext,
  bindReportContext,
  openReport,
  activateReportTab,
  closeReportTab,
  renderReportTabs,
  analysisOpen,
  analysisClose,
  appendSystemHint,
  renderReport,
  saveToReport,
  refreshHistory,
  updateBadge,
  refreshAllReportHistory,
  renderAllReportHistory,
  enableGcSidebar,
  renderGcSidebar,
  updateReportHeader,
  toggleGcSidebar,
};

Object.assign(app, {
  reportScopeUrl,
  addActiveReportContext,
  removeActiveReportContext,
  clearActiveReportContext,
  removeActiveReportContextByReport,
  bindReportBulkActions,
  deleteReportEntries,
  renderActiveReportContext,
  bindReportContext,
  openReport,
  activateReportTab,
  closeReportTab,
  renderReportTabs,
  analysisOpen,
  analysisClose,
  appendSystemHint,
  renderReport,
  saveToReport,
  refreshHistory,
  updateBadge,
  refreshAllReportHistory,
  renderAllReportHistory,
  enableGcSidebar,
  renderGcSidebar,
  updateReportHeader,
  toggleGcSidebar,
});
