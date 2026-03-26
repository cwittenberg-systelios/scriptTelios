import { createRoot } from "react-dom/client";
import App from "./klinische-dokumentation.jsx";

// Warte auf DOM dann mounte in den Confluence-Container
window.addEventListener("load", function() {
  var container = document.querySelector('[id^="systelios-root-"]');
  if (!container) {
    container = document.createElement("div");
    container.id = "systelios-root";
    document.body.appendChild(container);
  }
  createRoot(container).render(<App />);
});
