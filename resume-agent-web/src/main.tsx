import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import CvTailorPage from "./CvTailorPage";
import "./index.css";

const isCvTailorRoute =
  window.location.pathname === "/cv-tailor" ||
  window.location.pathname.startsWith("/cv-tailor/");

createRoot(document.getElementById("root")!).render(
  <StrictMode>{isCvTailorRoute ? <CvTailorPage /> : <App />}</StrictMode>
);
