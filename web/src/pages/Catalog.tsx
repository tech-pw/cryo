import { PageHeader } from "../components/common";
import { CatalogDashboard } from "./catalog/CatalogDashboard";
import { TableExplorerSection } from "./catalog/TableExplorerSection";

/**
 * Consolidated Catalog page.
 *
 * Replaces the legacy `/catalogs` (MultiCatalog) and `/tables` (TableExplorer) pages.
 * Single scroll: the fleet-wide dashboard widgets (per-catalog KPIs, cost
 * distribution pie chart, per-catalog health table) appear at the top, and the
 * filterable, paginated table explorer with bulk-assign lives below them.
 *
 * Cross-link contract: clicking a row in the dashboard's "Catalogs" table navigates to
 * `/catalogs?source=<source-id>`, which the table explorer picks up via `?source=`
 * and pre-applies a source filter -- so the dashboard-to-tables drill-down still works,
 * just on one URL instead of two.
 *
 * Bare `/tables` redirects to `/catalogs` (query string preserved); `/tables/:id` remains
 * table detail.
 */
export const Catalog = () => (
  <div>
    <PageHeader
      title="Catalogs & tables"
      subtitle="Fleet-wide datalake health at the top, filterable table explorer below."
      breadcrumbs={false}
    />
    <CatalogDashboard />
    <TableExplorerSection />
  </div>
);