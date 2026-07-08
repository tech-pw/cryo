import { useEffect, useLayoutEffect, useState } from "react";

export const PageTitle = ({ title, suffix = "Cryo" }: { title: string; suffix?: string }) => {
  const [renderFallbackHeading, setRenderFallbackHeading] = useState(false);

  useEffect(() => {
    const prev = document.title;
    document.title = title ? `${title} · ${suffix}` : suffix;
    return () => {
      document.title = prev;
    };
  }, [title, suffix]);

  useLayoutEffect(() => {
    const hasExistingH1 = document.querySelector("h1:not([data-page-title-fallback])") !== null;
    setRenderFallbackHeading(!hasExistingH1);
  }, [title]);

  if (!renderFallbackHeading) {
    return null;
  }

  return (
    <h1
      data-page-title-fallback
      style={{
        position: "absolute",
        width: 1,
        height: 1,
        padding: 0,
        margin: -1,
        overflow: "hidden",
        clip: "rect(0, 0, 0, 0)",
        whiteSpace: "nowrap",
        border: 0
      }}
    >
      {title}
    </h1>
  );
};