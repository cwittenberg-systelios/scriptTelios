import { createRoot } from "react-dom/client";
import App from "./klinische-dokumentation.jsx";

function mount() {
  // Confluence-Macro-Container (id beginnt mit "systelios-root-")
  var container = document.querySelector('[id^="systelios-root-"]');
  if (!container) {
    container = document.createElement("div");
    container.id = "systelios-root";
    document.body.appendChild(container);
  }
  createRoot(container).render(<App />);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mount);
} else {
  mount();
}
