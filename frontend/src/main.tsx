import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import "@xyflow/react/dist/style.css";
// Tailwind + shell tokens first; the legacy canvas's styles.css is unlayered
// and still wins over Tailwind's preflight for the WORKFLOW canvas.
import "./theme.css";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
