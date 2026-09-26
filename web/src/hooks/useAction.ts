import { useCallback, useEffect, useRef, useState } from "react";
import { haptic } from "../telegram";

export interface ActionState {
  busy: boolean;
  error: unknown;
  notice: string;
  /** Runs one mutation at a time; resolves to true on success. Never rejects. */
  run: (action: () => Promise<string | void>) => Promise<boolean>;
  clear: () => void;
}

/** Busy / error / success-notice state for button-triggered mutations. */
export function useAction(): ActionState {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState("");
  const inFlight = useRef(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const run = useCallback(async (action: () => Promise<string | void>) => {
    if (inFlight.current) return false;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    setNotice("");
    try {
      const message = await action();
      if (mounted.current && message) setNotice(message);
      haptic("success");
      return true;
    } catch (err) {
      if (mounted.current) setError(err);
      haptic("error");
      return false;
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy(false);
    }
  }, []);

  const clear = useCallback(() => {
    setError(null);
    setNotice("");
  }, []);

  return { busy, error, notice, run, clear };
}
