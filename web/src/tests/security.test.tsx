import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RichText } from "../components/RichText";
import {
  sanitizeHexColor,
  sanitizeSvgPath,
  VisualArtifactWindow,
} from "../components/VisualArtifactWindow";
import { clearAuthToken, getAuthToken } from "../lib/authToken";
import type { AudioEngine } from "../sphere/AudioEngine";
import { useJarvisStore } from "../store/useJarvisStore";

describe("securite du rendu Markdown et des artefacts SVG", () => {
  it("neutralise les balises HTML, liens javascript:/data: et images distantes dans RichText", () => {
    const hostile = [
      "<script>window.__xss = 1</script>",
      '<img src="https://attacker.tld/pixel.png" onerror="alert(1)" />',
      "[cliquez](javascript:alert(1))",
      "![tracker](https://attacker.tld/leak.png)",
      "**gras <img src=x onerror=alert(1)>** et `data:text/html,<svg onload=alert(1)>`",
    ].join("\n\n");

    const { container } = render(<RichText content={hostile} />);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent).toContain("<script>window.__xss = 1</script>");
  });

  it("valide strictement les couleurs hexadecimales et svg_path cote frontend", () => {
    expect(sanitizeHexColor("#22c55e")).toBe("#22c55e");
    expect(sanitizeHexColor("url(https://attacker.tld/leak.svg#p)")).toBe("#38bdf8");
    expect(sanitizeHexColor("expression(alert(1))")).toBe("#38bdf8");

    expect(sanitizeSvgPath("M 0 0 L 24 12 Z")).toBe("M 0 0 L 24 12 Z");
    expect(sanitizeSvgPath('M 0 0" onload="alert(1)')).toBeNull();
    expect(sanitizeSvgPath("url(https://attacker.tld/x)")).toBeNull();
    expect(sanitizeSvgPath("<image href='https://attacker.tld/p.png'/>")).toBeNull();
  });

  it("n'injecte jamais url(...) ni svg_path invalide dans le DOM SVG de VisualArtifactWindow", () => {
    const fakeAudio = {
      speakText: async () => false,
      stopPlayback: () => undefined,
    } as unknown as AudioEngine;

    useJarvisStore.setState({
      activeArtifact: {
        id: "art_sec_1",
        title: "Test SVG hostile",
        subtitle: "",
        mode: "2d",
        category: "custom",
        auto_play: false,
        elements: [
          {
            id: "node1",
            kind: "svg_path",
            label: "<script>alert(1)</script>",
            sublabel: null,
            color: "url(https://attacker.tld/leak.svg#f)",
            x: 50,
            y: 50,
            z: 0,
            width: 14,
            height: 10,
            from_id: null,
            to_id: null,
            from_pin: null,
            to_pin: null,
            svg_path: 'M 0 0" onclick="alert(1)',
          },
          {
            id: "w1",
            kind: "wire",
            label: "Lien",
            sublabel: null,
            color: "url(https://attacker.tld/wire.svg#w)",
            x: 50,
            y: 50,
            z: 0,
            width: 10,
            height: 10,
            from_id: "node1",
            to_id: "node1",
            from_pin: null,
            to_pin: null,
            svg_path: null,
          },
        ],
        steps: [
          {
            index: 0,
            title: "Etape 1",
            spoken_text: "Test",
            annotation: "",
            reveal_elements: ["node1", "w1"],
            highlight_elements: [],
            active_connections: [],
          },
        ],
      },
      artifactStepIndex: 0,
      artifactPlaying: false,
      artifactMinimized: false,
      artifactViewMode: "2d",
    });

    const { container } = render(<VisualArtifactWindow audioEngine={fakeAudio} />);
    const svg = container.querySelector("svg.artifact-svg");
    expect(svg).not.toBeNull();
    expect(svg?.innerHTML).not.toContain("attacker.tld");
    expect(svg?.querySelector("script")).toBeNull();
    expect(svg?.querySelector("image")).toBeNull();
    const customPaths = svg?.querySelectorAll('[data-element-id="node1"] path');
    expect(customPaths?.length ?? 0).toBe(0);

    useJarvisStore.getState().closeArtifact();
  });

  it("migre automatiquement un jeton legacy localStorage vers sessionStorage des la lecture", () => {
    clearAuthToken();
    window.localStorage.setItem("jarvis_api_token", "legacy-secret-token");
    expect(getAuthToken()).toBe("legacy-secret-token");
    expect(window.sessionStorage.getItem("jarvis_api_token")).toBe("legacy-secret-token");
    expect(window.localStorage.getItem("jarvis_api_token")).toBeNull();
    clearAuthToken();
  });
});
