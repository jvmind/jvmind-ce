import { app } from "../app.js";
import { refreshHistory, refreshAllReportHistory } from "./history.js";

export function analysisOpen() {
  document.getElementById("gcPanel").classList.add("open");
  Promise.resolve(refreshHistory()).catch(e => console.warn("refreshHistory failed:", e));
  Promise.resolve(app.refreshJstackHistory()).catch(e => console.warn("refreshJstackHistory failed:", e));
  Promise.resolve(app.refreshHeapdumpHistory && app.refreshHeapdumpHistory()).catch(e => console.warn("refreshHeapdumpHistory failed:", e));
  Promise.resolve(refreshAllReportHistory()).catch(e => console.warn("refreshAllReportHistory failed:", e));
}

export function analysisClose() {
  document.getElementById("gcPanel").classList.remove("open");
}
