import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import { App } from "./app/App";
import "./styles/tokens.css";
import "./styles/app.css";

const root = document.getElementById("root");
if (!root) throw new Error("#root is missing from index.html");

createRoot(root).render(
  <StrictMode>
    <BrowserRouter basename="/console">
      <App />
    </BrowserRouter>
  </StrictMode>,
);
