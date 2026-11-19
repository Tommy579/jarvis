import type { SlashCommandDef } from "../lib/slashCommands";

interface SlashCommandMenuProps {
  items: SlashCommandDef[];
  selectedIndex: number;
  onSelect: (item: SlashCommandDef) => void;
  onHover?: (index: number) => void;
}

export function SlashCommandMenu({
  items,
  selectedIndex,
  onSelect,
  onHover,
}: SlashCommandMenuProps) {
  if (items.length === 0) return null;

  return (
    <div
      className="slash-menu"
      role="listbox"
      aria-label="Commandes slash disponibles"
    >
      {items.map((item, idx) => (
        <button
          key={item.cmd}
          type="button"
          className="slash-item"
          role="option"
          aria-selected={idx === selectedIndex}
          onMouseDown={(e) => {
            // Garde le focus sur le textarea et évite le blur avant le clic
            e.preventDefault();
          }}
          onMouseEnter={() => onHover?.(idx)}
          onClick={() => onSelect(item)}
        >
          <span className="slash-main">
            <span className="slash-cmd">{item.cmd}</span>
            {item.arg && <span className="slash-arg">{item.arg}</span>}
          </span>
          <span className="slash-desc">{item.desc}</span>
        </button>
      ))}
    </div>
  );
}
