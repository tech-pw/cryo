import { useEffect } from "react";

export const PageTitle = ({ title, suffix = "Cryo" }: { title: string; suffix?: string }) => {
  useEffect(() => {
    const prev = document.title;
    document.title = title ? `${title} · ${suffix}` : suffix;
    return () => {
      document.title = prev;
    };
  }, [title, suffix]);
  return null;
};