import { ArrowLeft, Home, Search as SearchIcon } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { PageHeader } from "../components/common";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";

const RELATED_DESTINATIONS: Array<{ to: string; label: string }> = [
  { to: "/health", label: "Datalake health" },
  { to: "/catalogs", label: "Catalogs" },
  { to: "/operations", label: "Operations" },
  { to: "/cost", label: "Cost" },
  { to: "/governance", label: "Governance" },
  { to: "/connections", label: "Connections" }
];

/** Authenticated-app wildcard fallback — reached for any path that matches no route. */
export const NotFound = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");

  const handleSearchSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = query.trim();
    navigate(trimmed ? `/search?q=${encodeURIComponent(trimmed)}` : "/search");
  };

  const handleGoBack = () => {
    if (window.history.length > 1) {
      navigate(-1);
    } else {
      navigate("/health");
    }
  };

  return (
    <div>
      <PageHeader
        title="Page not found"
        subtitle="This link doesn't match any Cryo page. Search for what you need or return to a known location."
        breadcrumbs={<></>}
      />
      <div className="max-w-[560px] rounded-[var(--radius-default)] border border-border bg-card p-6">
        <p className="text-sm text-muted-foreground">
          <code className="rounded bg-[var(--color-surface-sunken)] px-1.5 py-0.5 text-xs">
            {location.pathname}
          </code>{" "}
          may have moved, been renamed, or the link may be out of date.
        </p>

        <form onSubmit={handleSearchSubmit} className="mt-4 flex flex-col gap-1.5">
          <Label htmlFor="not-found-search">Search catalogs, tables, and columns</Label>
          <div className="flex gap-2">
            <Input
              id="not-found-search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Table, schema, or catalog name"
            />
            <Button type="submit" variant="primary">
              <SearchIcon aria-hidden="true" />
              Search
            </Button>
          </div>
        </form>

        <div className="mt-5 flex flex-wrap gap-2">
          <Link to="/health">
            <Button variant="primary">
              <Home aria-hidden="true" />
              Go to Datalake health
            </Button>
          </Link>
          <Button variant="secondary" onClick={handleGoBack}>
            <ArrowLeft aria-hidden="true" />
            Go back
          </Button>
        </div>

        <nav aria-label="Other destinations" className="mt-5">
          <p className="text-sm font-medium text-foreground">Or jump to</p>
          <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm">
            {RELATED_DESTINATIONS.map((destination) => (
              <li key={destination.to}>
                <Link className="text-primary hover:underline" to={destination.to}>
                  {destination.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </div>
  );
};
