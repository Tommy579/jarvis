export function normalizeVoiceCommandText(text: string): string {
  return (text || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

const STOP_ACTION_VERB =
  "(?:coupe|couper|arrete|arreter|stop|stoppe|stopper|desactive|desactiver|ferme|fermer|eteins|eteindre|termine|terminer|finis|finir|cesse|cesser|mets|met|mettre|passe|passer|lache|lacher)";
const STOP_TARGET =
  "(?:record|enregistrement|ecoute|ecouter|micro|microphone|capture|audio|veille|standby)";

const STOP_RECORD_PATTERNS: RegExp[] = [
  // Verbe d'arret suivi (avec jusqu'a 3 mots intermediaires) de la cible :
  // "coupe le record", "tu peux arreter l'enregistrement stp", "coupe le micro maintenant", "mets toi en veille"
  new RegExp(`\\b${STOP_ACTION_VERB}(?:\\s+[a-z0-9]+){0,3}\\s+${STOP_TARGET}\\b`, "i"),
  // Formes infinitives / verbales avec "d'ecouter" ou "de m'ecouter"
  /\b(?:arrete|arreter|cesse|cesser|finis|finir|stop|stoppe)\s+(?:de\s+(?:m\s+|nous\s+)?|d\s+)ecouter\b/i,
  // Formes nominales ou courtes explicites
  /\b(?:fin\s+(?:d|de\s+l|du)\s*(?:record|enregistrement|ecoute)|(?:record|ecoute|micro)\s+off|pause\s+(?:micro|ecoute|record)|mode\s+veille)\b/i,
  // Formules naturelles de cloture d'echange
  /\b(?:c\s+est\s+tout\s+pour\s+(?:le\s+moment|l\s+instant|maintenant|aujourd\s+hui)|bonne\s+nuit\s+jarvis|au\s+revoir\s+jarvis)\b/i,
];

const NON_COMMAND_QUESTION_RE = /^(?:comment|pourquoi|explique|qu\s+est\s+ce|c\s+est\s+quoi|peux\s+tu\s+m\s+expliquer)\b/i;

/**
 * Indique si la phrase transcrite demande d'arreter l'ecoute active
 * ("coupe le record", "arrete l'enregistrement", "coupe le micro", "mets-toi en veille", etc.).
 */
export function isStopRecordCommand(transcript: string): boolean {
  const norm = normalizeVoiceCommandText(transcript);
  if (!norm) return false;
  const wordCount = norm.split(/\s+/).length;
  if (wordCount > 14) return false;
  if (NON_COMMAND_QUESTION_RE.test(norm)) return false;
  return STOP_RECORD_PATTERNS.some((re) => re.test(norm));
}

const WAKE_WORD_PATTERN = "(?:(?:hey|ok|okay|dis|salut)\\s+)?(?:j\\s*'?\\s*arvis|jarvisse|jarvice|djarvis)";
const WAKE_WORD_RE = new RegExp(`\\b${WAKE_WORD_PATTERN}\\b[,:;.!?\\s-]*(.*)`, "i");

/**
 * Detecte le mot d'activation "jarvis" (et variantes phonetiques proches) en mot entier.
 * Retourne `triggered=true` et la requete eventuelle qui suit le wake word.
 */
export function extractWakeWord(transcript: string): { triggered: boolean; query: string } {
  const raw = (transcript || "").trim();
  if (!raw) return { triggered: false, query: "" };

  const norm = raw
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "");
  const m = WAKE_WORD_RE.exec(norm);
  if (!m) return { triggered: false, query: "" };

  const rawMatch = new RegExp(`\\b${WAKE_WORD_PATTERN}\\b[,:;.!?\\s-]*(.*)`, "i").exec(raw);
  const query = (rawMatch ? rawMatch[1] : m[1] ?? "").trim();
  return { triggered: true, query };
}

export const VOICE_INACTIVITY_TIMEOUT_MS = 10_000;

/**
 * Determine si l'ecoute active ("record") doit s'arreter automatiquement apres
 * un delai sans voix reconnue, sans couper pendant une transcription, une reflexion
 * ou une reponse vocale de Jarvis.
 */
export function shouldAutoStopVoiceRecord(params: {
  voiceMode: "off" | "standby" | "active";
  sphereState: string;
  isTranscribing: boolean;
  isPlayingAudio: boolean;
  lastRecognizedAtMs: number;
  nowMs: number;
  timeoutMs?: number;
}): boolean {
  if (params.voiceMode !== "active") return false;
  if (params.isTranscribing || params.isPlayingAudio) return false;
  if (params.sphereState === "thinking" || params.sphereState === "speaking") return false;
  const timeout = params.timeoutMs ?? VOICE_INACTIVITY_TIMEOUT_MS;
  return params.nowMs - params.lastRecognizedAtMs >= timeout;
}
