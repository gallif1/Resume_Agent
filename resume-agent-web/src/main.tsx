import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import CvTailorPage from "./CvTailorPage";
import JobApplyPage from "./JobApplyPage";
import LiveJobScannerPage from "./LiveJobScannerPage";
import "./index.css";

const path = window.location.pathname;
const isCvTailorRoute =
  path === "/cv-tailor" || path.startsWith("/cv-tailor/");
const isJobApplyRoute =
  path === "/job-apply" || path.startsWith("/job-apply/");
const isLiveScannerRoute =
  path === "/live-scanner" || path.startsWith("/live-scanner/");

function Root() {
  if (isCvTailorRoute) return <CvTailorPage />;
  if (isJobApplyRoute) return <JobApplyPage />;
  if (isLiveScannerRoute) return <LiveJobScannerPage />;
  return <App />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>
);
