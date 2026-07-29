import { useEffect, useState } from "react";
import { cn } from "../../lib/cn";

export interface TableDetailSection {
  id: string;
  label: string;
}

interface SectionNavProps {
  sections: TableDetailSection[];
}

export const SectionNav = ({ sections }: SectionNavProps) => {
  const [active, setActive] = useState(sections[0]?.id ?? "");

  useEffect(() => {
    if (!sections.length) return;
    if (typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio);
        if (visible[0]?.target?.id) {
          setActive(visible[0].target.id);
        }
      },
      { rootMargin: "-20% 0px -65% 0px", threshold: [0.2, 0.4, 0.7] }
    );

    for (const section of sections) {
      const element = document.getElementById(section.id);
      if (element) observer.observe(element);
    }

    return () => observer.disconnect();
  }, [sections]);

  return (
    <nav className="td-section-nav-rail" aria-label="Table detail sections">
      {sections.map((section) => (
        <button
          key={section.id}
          type="button"
          className={cn(
            "td-section-nav-rail__item",
            active === section.id && "td-section-nav-rail__item--active"
          )}
          onClick={() => {
            document.getElementById(section.id)?.scrollIntoView({ behavior: "smooth", block: "start" });
          }}
        >
          {section.label}
        </button>
      ))}
    </nav>
  );
};
