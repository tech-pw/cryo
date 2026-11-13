import { useEffect, useRef, useState } from "react";
import { readReducedMotion } from "../../lib/motion";

const DURATION_MS = 420;

/** Counts from zero to `target` once, on the first real value. Later updates jump straight there. */
export const useCountUp = (target: number | null): number | null => {
  const animated = useRef(false);
  const [value, setValue] = useState<number | null>(target);
  useEffect(() => {
    if (target == null) {
      setValue(null);
      return;
    }
    if (animated.current || readReducedMotion() || typeof requestAnimationFrame !== "function") {
      animated.current = true;
      setValue(target);
      return;
    }
    animated.current = true;
    let frame = 0;
    let start: number | null = null;
    let done = false;
    const step = (now: number) => {
      start ??= now;
      const t = Math.min(1, (now - start) / DURATION_MS);
      setValue(target * (1 - (1 - t) ** 3));
      if (t < 1) frame = requestAnimationFrame(step);
      else done = true;
    };
    setValue(0);
    frame = requestAnimationFrame(step);
    return () => {
      cancelAnimationFrame(frame);
      // An interrupted first run (StrictMode remount, fast data change) may animate again.
      if (!done) animated.current = false;
      setValue(target);
    };
  }, [target]);
  return value;
};
