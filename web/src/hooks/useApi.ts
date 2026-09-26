import { useCallback, useEffect, useState } from "react";
import { isAbort } from "../errors";

export interface ApiState<T> {
  data: T | null;
  error: unknown;
  loading: boolean;
  /** Re-run the loader (keeps the old data visible while loading). */
  reload: () => void;
  /** Replace data locally after a mutation. */
  setData: (next: T | null) => void;
}

/**
 * Load data with an AbortController: a newer call or unmount cancels the
 * previous request, so no state is set on an unmounted component and a
 * slow old response never overwrites a newer one.
 *
 * `loader` must be stable (wrap it in useCallback) — it is the dependency.
 */
export function useApi<T>(loader: (signal: AbortSignal) => Promise<T>): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    loader(controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setData(value);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted && !isAbort(err)) setError(err);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [loader, tick]);

  const reload = useCallback(() => setTick((value) => value + 1), []);
  return { data, error, loading, reload, setData };
}
