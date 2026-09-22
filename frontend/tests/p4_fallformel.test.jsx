/**
 * v19.28 / v19.28.1 (S5): P4 - Struktur-Schalter und strukturierter Fallformel-Editor (React).
 */
import { describe, test, expect, jest } from "@jest/globals";
import { render, screen, fireEvent } from "@testing-library/react";
import { StrukturSwitch, FallformelCard, P4_DRAFT_DEFAULT, EMPTY_FF } from "../src/panels/P4.jsx";
import { P_ENTL } from "../src/prompt-defaults.jsx";
import { parseFallformel } from "../src/fallformel.js";

const FF_TEXT = `### Auftrag
A

### Themenkandidaten
1. **Thema Eins** – eins Belege: (Einzel 01.01.)
2. **Thema Zwei** – zwei
3. **Thema Drei** – drei

### Wendepunkte je Modalität
- Einzeltherapie: Elternbesuch (13.01.)
- Gruppentherapie: keine Wendepunkte dokumentiert
- Nonverbale Therapien: Fatman (08.01.)

### Symptomveränderung
s

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
  test("Draft-Default ist Status quo mit Standard-Prompt, Fallformel strukturiert", () => {
    expect(P4_DRAFT_DEFAULT.struktur).toBe("modalitaet");
    expect(P4_DRAFT_DEFAULT.prompt).toBe(P_ENTL);
    expect(P4_DRAFT_DEFAULT.fallformel).toBeNull();
    expect(Object.keys(EMPTY_FF().wendepunkte)).toHaveLength(3);
  });
});

function renderCard(overrides = {}) {
  const props = {
    ff: parseFallformel(FF_TEXT), proposalText: FF_TEXT,
    audit: { applied: true, source: "llm", issues: [{ severity: "high", detail: "Datum 24.12. ohne Quelle" }] },
    selection: [0, 1, 2], onChange: jest.fn(), onSelection: jest.fn(), onReset: jest.fn(), onRerun: jest.fn(),
    rerunDisabled: false, busy: false, ...overrides,
  };
  render(<FallformelCard {...props} />);
  return props;
}

describe("FallformelCard (strukturiert)", () => {
  test("rendert Felder statt Markdown, Nummern = Reihenfolge", () => {
    renderCard();
    expect(screen.queryByLabelText("Fallformel")).toBeNull();               // keine Markdown-Textarea mehr
    expect(screen.getByLabelText("Auftrag")).toHaveValue("A");
    expect(screen.getByLabelText("Titel Thema 1")).toHaveValue("Thema Eins");
    expect(screen.getByLabelText("Belege Thema 1")).toHaveValue("(Einzel 01.01.)");
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes).toHaveLength(3);
    expect(boxes[2]).toHaveTextContent("3");
    expect(screen.getByLabelText("Einzeltherapie Wendepunkt 1")).toHaveValue("Elternbesuch (13.01.)");
    expect(screen.getByText(/Datum 24.12. ohne Quelle/)).toBeInTheDocument();
    expect(screen.getByText(/Gesendet werden 3 Themen, 2 Wendepunkte/)).toBeInTheDocument();
  });

  test("Abwaehlen wirkt sofort auf Auswahl und Vorschau", () => {
    const p = renderCard({ selection: [0, 2] });
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes[1]).toHaveAttribute("aria-checked", "false");
    expect(boxes[2]).toHaveTextContent("2");                                  // Thema Drei ist jetzt Nr. 2
    fireEvent.click(boxes[2]);
    expect(p.onSelection).toHaveBeenCalledWith([0]);
    const preview = screen.getByTestId("ff-preview").textContent;
    expect(preview).toContain("1. **Thema Eins**");
    expect(preview).toContain("2. **Thema Drei**");
    expect(preview).not.toContain("Thema Zwei");                              // abgewaehlt = weggelassen
    expect(screen.getByText(/Gesendet werden 2 Themen/)).toBeInTheDocument();
  });

  test("ueber MAX hinaus nicht waehlbar; Feld-Edit ruft onChange mit Struktur", () => {
    const p = renderCard({ selection: [0, 1, 2] });
    // alle drei gewaehlt; ein viertes wuerde disabled sein - hier: abwaehlen dann Titel aendern
    fireEvent.change(screen.getByLabelText("Titel Thema 2"), { target: { value: "Neuer Titel" } });
    expect(p.onChange).toHaveBeenCalled();
    const ff = p.onChange.mock.calls[0][0];
    expect(ff.themen[1].titel).toBe("Neuer Titel");
    expect(ff.themen[1].desc).toBe("zwei");
  });

  test("Wendepunkt hinzufuegen/entfernen und eigenes Thema", () => {
    const p = renderCard();
    fireEvent.click(screen.getAllByText("+ Wendepunkt")[1]);                 // Gruppentherapie
    expect(p.onChange.mock.calls.at(-1)[0].wendepunkte.Gruppentherapie).toEqual([""]);
    fireEvent.click(screen.getByLabelText("Einzeltherapie Wendepunkt 1 entfernen"));
    expect(p.onChange.mock.calls.at(-1)[0].wendepunkte.Einzeltherapie).toEqual([]);
    fireEvent.click(screen.getByText("+ Eigenes Thema hinzufügen"));
    expect(p.onChange.mock.calls.at(-1)[0].themen).toHaveLength(4);
    expect(p.onSelection).not.toHaveBeenCalled();                            // schon 3 gewaehlt -> nicht automatisch dazu
  });

  test("Reihenfolge aendern verschiebt Auswahl mit; Entfernen re-indiziert", () => {
    const p = renderCard({ selection: [0, 2] });
    fireEvent.click(screen.getByLabelText("Thema 3 nach oben"));
    expect(p.onChange.mock.calls.at(-1)[0].themen.map(t => t.titel)).toEqual(["Thema Eins", "Thema Drei", "Thema Zwei"]);
    expect(p.onSelection).toHaveBeenLastCalledWith([0, 1]);
    fireEvent.click(screen.getByLabelText("Thema 1 entfernen"));
    expect(p.onSelection).toHaveBeenLastCalledWith([1]);
  });

  test("Rerun-Button, Reset und Fallback-Hinweis", () => {
    const p = renderCard({ audit: { applied: false, fallback_reason: "Ollama down" }, ff: EMPTY_FF(), selection: [], rerunDisabled: true });
    expect(screen.getByText(/keine Fallformel erstellt/)).toBeInTheDocument();
    expect(screen.getByText(/Ollama down/)).toBeInTheDocument();
    expect(screen.getByText(/Kein Thema gewählt/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /neu generieren/i })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /wiederherstellen/i }));
    expect(p.onReset).toHaveBeenCalled();
  });
});
