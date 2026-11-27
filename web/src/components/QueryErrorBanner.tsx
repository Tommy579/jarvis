import { describeApiError } from "../lib/apiErrors";
import { ErrorBanner } from "./ui";

/**
 * Sous-ensemble structurel d'un resultat `useQuery` de TanStack Query.
 * Volontairement minimal pour accepter n'importe quelle query sans generique.
 */
export interface RetryableQuery {
  isError: boolean;
  error: unknown;
  isFetching: boolean;
  refetch: () => Promise<unknown>;
}

/**
 * Bandeau d'erreur discret pour une requete de page : affiche le message en
 * francais et un bouton « Réessayer » qui relance uniquement ce fetch.
 */
export function QueryErrorBanner({
  query,
  fallback,
  testId,
}: {
  query: RetryableQuery;
  fallback: string;
  testId?: string;
}) {
  if (!query.isError) return null;
  return (
    <ErrorBanner
      message={describeApiError(query.error, fallback)}
      onRetry={() => query.refetch()}
      retrying={query.isFetching}
      testId={testId}
    />
  );
}
