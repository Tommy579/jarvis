import { useEffect, useRef, useState } from "react";
import { Icon, type IconName } from "./Icon";

export interface CommandItem {
  id: string;
  label: string;
  hint: string;
  icon?: IconName;
  action: () => void;
}

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  commands: CommandItem[];
}

function normalize(s: string): string {
  return s
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}

export function CommandPalette({ open, onClose, commands }: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLUListElement | null>(null);
  const restoreFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (open) {
      restoreFocus.current = document.activeElement as HTMLElement | null;
      setQuery("");
      setActive(0);
    } else {
      restoreFocus.current?.focus?.();
    }
  }, [open]);

  if (!open) return null;

  const q = normalize(query.trim());
  const filtered = commands.filter((cmd) => normalize(`${cmd.label} ${cmd.hint}`).includes(q));
  const safeActive = Math.min(active, Math.max(0, filtered.length - 1));

  const run = (cmd: CommandItem) => {
    onClose();
    cmd.action();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((safeActive + 1) % Math.max(1, filtered.length));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((safeActive - 1 + filtered.length) % Math.max(1, filtered.length));
    } else if (e.key === "Enter" && filtered[safeActive]) {
      e.preventDefault();
      run(filtered[safeActive]);
    } else if (e.key === "Tab") {
      e.preventDefault();
    }
  };

  return (
    <div className="palette-backdrop" onClick={onClose}>
      <div
        className="palette"
        role="dialog"
        aria-modal="true"
        aria-label="Palette de commandes"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={onKeyDown}
      >
        <div className="palette__search">
          <Icon name="search" size={18} />
          <input
            type="text"
            autoFocus
            role="combobox"
            aria-expanded="true"
            aria-controls="palette-list"
            aria-activedescendant={filtered[safeActive] ? `cmd-${filtered[safeActive].id}` : undefined}
            aria-label="Rechercher une commande"
            placeholder="Aller à un écran ou lancer une commande…"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setActive(0);
            }}
          />
          <span className="kbd">Échap</span>
        </div>
        {filtered.length > 0 ? (
          <ul className="palette__list" id="palette-list" role="listbox" ref={listRef}>
            {filtered.map((cmd, i) => (
              <li key={cmd.id} role="presentation">
                <button
                  id={`cmd-${cmd.id}`}
                  type="button"
                  role="option"
                  aria-selected={i === safeActive}
                  tabIndex={-1}
                  className="palette__item"
                  data-active={i === safeActive}
                  onMouseMove={() => setActive(i)}
                  onClick={() => run(cmd)}
                >
                  <span style={{ display: "flex", alignItems: "center", gap: 12 }}>
                    {cmd.icon && <Icon name={cmd.icon} size={16} />}
                    {cmd.label}
                  </span>
                  <span className="palette__hint">{cmd.hint}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <div className="palette__empty">Aucune commande ne correspond à « {query} ».</div>
        )}
      </div>
    </div>
  );
}
