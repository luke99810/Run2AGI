import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { HomeView } from "@desktop/views/HomeView";
import "./styles.css";

window.history.scrollRestoration = "manual";

const rootElement = document.getElementById("root");

if (rootElement === null) {
  throw new Error("找不到 React 根节点 #root");
}

createRoot(rootElement).render(
  <StrictMode>
    <HomeView />
  </StrictMode>,
);
