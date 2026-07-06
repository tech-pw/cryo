import type { ReactNode } from "react";

interface DetailPageProps {
  hero?: ReactNode;
  kpis?: ReactNode;
  sectionNav?: ReactNode;
  children: ReactNode;
}

export const DetailPage = ({ hero, kpis, sectionNav, children }: DetailPageProps) => (
  <div className="table-detail">
    {hero}
    {kpis}
    {sectionNav}
    {children}
  </div>
);
