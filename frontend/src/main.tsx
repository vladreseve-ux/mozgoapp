import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { initializeTelegram } from "./telegram";
import "./styles.css";

const initData = initializeTelegram();
const demoMode = import.meta.env.VITE_DEMO_MODE === "true" || !initData;

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App initData={initData} demoMode={demoMode} />
  </React.StrictMode>,
);
