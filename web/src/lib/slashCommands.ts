export type SlashCommandType = "workspace" | "route" | "immediate" | "goal";

export interface SlashCommandDef {
  cmd: string;
  arg: string;
  desc: string;
  type: SlashCommandType;
  aliases?: string[];
}

export const SLASH_COMMANDS: SlashCommandDef[] = [
  {
    cmd: "/workspace",
    arg: "<dossier>",
    desc: "Définir le dossier de travail actif (ex: Documents/marges)",
    type: "workspace",
  },
  {
    cmd: "/skills",
    arg: "",
    desc: "Gérer les skills, outils et serveurs MCP",
    type: "immediate",
    aliases: ["/skill"],
  },
  {
    cmd: "/usage",
    arg: "",
    desc: "Afficher les usages IA (jetons consommés et coût du jour)",
    type: "immediate",
    aliases: ["/tokens"],
  },
  {
    cmd: "/model",
    arg: "",
    desc: "Afficher les modèles configurés et le mode actif",
    type: "immediate",
    aliases: ["/models"],
  },
  {
    cmd: "/local",
    arg: "[message]",
    desc: "Forcer le modèle local (pour la session ou ce message)",
    type: "route",
  },
  {
    cmd: "/council",
    arg: "[message]",
    desc: "Forcer le modèle avancé / conseil (pour la session ou ce message)",
    type: "route",
    aliases: ["/moa"],
  },
  {
    cmd: "/auto",
    arg: "",
    desc: "Rétablir le modèle par défaut / routage automatique",
    type: "immediate",
  },
  {
    cmd: "/ask",
    arg: "<demande>",
    desc: "Notifier pour les commandes terminal à partir du niveau 3",
    type: "route",
  },
  {
    cmd: "/status",
    arg: "",
    desc: "Vérifier l'état de jarvis-core, WebSocket, capteurs et MCP",
    type: "immediate",
  },
  {
    cmd: "/clear",
    arg: "",
    desc: "Démarrer une nouvelle conversation et effacer le fil",
    type: "immediate",
    aliases: ["/new"],
  },
  {
    cmd: "/goal",
    arg: "<objectif>",
    desc: "Lancer un objectif autonome jusqu'à vérification",
    type: "goal",
  },
  {
    cmd: "/settings",
    arg: "",
    desc: "Ouvrir les paramètres et la configuration",
    type: "immediate",
    aliases: ["/config"],
  },
  {
    cmd: "/tint",
    arg: "",
    desc: "Basculer l'accent visuel (laiton / bleu Jarvis)",
    type: "immediate",
    aliases: ["/theme"],
  },
  {
    cmd: "/help",
    arg: "",
    desc: "Afficher toutes les commandes disponibles",
    type: "immediate",
    aliases: ["/aide"],
  },
];

export function filterSlashCommands(inputValue: string): SlashCommandDef[] {
  if (!inputValue.startsWith("/") || inputValue.includes("\n")) {
    return [];
  }
  const trimmed = inputValue.trimStart();
  const spaceIdx = trimmed.indexOf(" ");
  if (spaceIdx !== -1) {
    return [];
  }
  const cmdPart = trimmed.toLowerCase();
  return SLASH_COMMANDS.filter(
    (c) =>
      c.cmd.startsWith(cmdPart) ||
      (c.aliases?.some((alias) => alias.startsWith(cmdPart)) ?? false)
  );
}
