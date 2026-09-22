/**
 * v19.28 (S5): P4 - Struktur-Schalter und Fallformel-Card (React).
 */
import { describe, test, expect, jest } from "@jest/globals";
import { render, screen, fireEvent } from "@testing-library/react";
import { StrukturSwitch, FallformelCard, P4_DRAFT_DEFAULT } from "../src/panels/P4.jsx";
import { P_ENTL } from "../src/prompt-defaults.jsx";

const FF = `### Auftrag
A

### Themenkandidaten
1. **Thema Eins** – eins
2. **Thema Zwei** – zwei
3. **Thema Drei** – drei

### Offene Themen
o`;

describe("StrukturSwitch", () => {
  test("Default Status quo, Umschalten ruft onChange", () => {
    const onChange = jest.fn();
    render(<StrukturSwitch value="modalitaet" onChange={onChange} />);
    const radios = screen.getAllByRole("radio");
    expect(radios[0]).toHaveAttribute("aria-checked", "true");
    fireEvent.click(radios[1]);
    expect(onChange).toHaveBeenCalledWith("thematisch");
  });
  test("Draft-Default ist Status quo mit Standard-Prompt", () => {
    expect(P4_DRAFT_DEFAULT.struktur).toBe("modalitaet");
    expect(P4_DRAFT_DEFAULT.prompt).toBe(P_ENTL);
  });
});

describe("FallformelCard", () => {
  test("zeigt Themen als Checkboxen, max. 3 waehlbar, Rerun-Button", () => {
    const onSelection = jest.fn();
    const onRerun = jest.fn();
    render(<FallformelCard text={FF} audit={{ applied: true, source: "llm", issues: [{ severity: "high", detail: "Datum 24.12. ohne Quelle" }] }}
      selection={[0, 1, 2]} onSelection={onSelection} onText={() => {}} onRerun={onRerun} rerunDisabled={false} busy={false} />);
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes).toHaveLength(3);
    fireEvent.click(boxes[1]);
    expect(onSelection).toHaveBeenCalledWith([0, 2]);
    expect(screen.getByText(/Datum 24.12. ohne Quelle/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /neu generieren/i }));
    expect(onRerun).toHaveBeenCalled();
  });
  test("Fallback-Hinweis ohne Fallformel", () => {
    render(<FallformelCard text="" audit={{ applied: false, fallback_reason: "Ollama down" }} selection={[]}
      onSelection={() => {}} onText={() => {}} onRerun={() => {}} rerunDisabled={true} busy={false} />);
    expect(screen.getByText(/keine Fallformel erstellt/)).toBeInTheDocument();
    expect(screen.getByText(/Ollama down/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /neu generieren/i })).toBeDisabled();
  });
});
