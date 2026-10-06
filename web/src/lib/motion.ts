import { useEffect, useRef, useState } from "react";

export const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

export const MOTION_MS = {
  instant: 0,
  fast: 120,
  base: 180,
  medium: 220,
  slow: 280,
  layout: 360,
  highlight: 800
} as const;

export const MOTION_EASING = {
  out: "cubic-bezier(0.16, 1, 0.3, 1)",
  in: "cubic-bezier(0.7, 0, 0.84, 0)",
  standard: "cubic-bezier(0.4, 0, 0.2, 1)"
} as const;

export const MOTION_VARS = {
  instant: "--motion-instant",
  fast: "--motion-fast",
  base: "--motion-base",
  medium: "--motion-medium",
  slow: "--motion-slow",
  layout: "--motion-layout",
  highlight: "--motion-highlight",
  easeOut: "--ease-out",
  easeIn: "--ease-in",
  easeStandard: "--ease-standard"
} as const;

export type MotionToken = keyof typeof MOTION_MS;

export function motionVar(token: MotionToken): string {
  return `var(${MOTION_VARS[token]}, ${MOTION_MS[token]}ms)`;
}

export function motionMs(token: MotionToken, reducedMotion = false): number {
  if (reducedMotion) return 0;
  return MOTION_MS[token];
}

export function readReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia(REDUCED_MOTION_QUERY).matches;
}

function subscribe(media: MediaQueryList, onChange: () => void): () => void {
  if (typeof media.addEventListener === "function") {
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }
  media.addListener(onChange);
  return () => media.removeListener(onChange);
}

export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(readReducedMotion);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const media = window.matchMedia(REDUCED_MOTION_QUERY);
    const onChange = () => setReduced(media.matches);
    setReduced(media.matches);
    return subscribe(media, onChange);
  }, []);

  return reduced;
}

export function animateOnce(reducedMotion: boolean, isFirstRender: boolean): boolean {
  return !reducedMotion && isFirstRender;
}

export function useAnimateOnce(): boolean {
  const reducedMotion = useReducedMotion();
  const isFirstRender = useRef(true);
  const active = animateOnce(reducedMotion, isFirstRender.current);
  useEffect(() => {
    isFirstRender.current = false;
  }, []);
  return active;
}
