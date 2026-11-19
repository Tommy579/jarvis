/**
 * Preambule des tests Vitest.
 *
 * Node 22+ expose un `globalThis.localStorage` experimental qui vaut `undefined` sans
 * `--localstorage-file`, et il masque celui de jsdom : les tests qui touchent au stockage
 * navigateur echouent alors que le navigateur reel fonctionne. On installe donc un
 * implementation en memoire conforme a l'interface Storage.
 */

class MemoryStorage implements Storage {
  private items = new Map<string, string>();

  get length(): number {
    return this.items.size;
  }

  clear(): void {
    this.items.clear();
  }

  getItem(key: string): string | null {
    return this.items.has(key) ? (this.items.get(key) as string) : null;
  }

  key(index: number): string | null {
    return Array.from(this.items.keys())[index] ?? null;
  }

  removeItem(key: string): void {
    this.items.delete(key);
  }

  setItem(key: string, value: string): void {
    this.items.set(key, String(value));
  }
}

function install(name: "localStorage" | "sessionStorage"): void {
  if (typeof window === "undefined") return;
  if (window[name]) return;
  Object.defineProperty(window, name, {
    value: new MemoryStorage(),
    configurable: true,
    writable: true,
  });
}

install("localStorage");
install("sessionStorage");
