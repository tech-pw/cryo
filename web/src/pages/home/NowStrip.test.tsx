import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { EngineLoadRow } from "../../api/engineLoad";
import { NowStrip } from "./NowStrip";

const engine = (name: string, used: number, limit: number): EngineLoadRow => ({
  destination_id: name,
  name,
  engine: "spark",
  slots_used: used,
  slots_limit: limit,
  admission: "open",
  live_probe: "ok"
});

describe("NowStrip", () => {
  it("keeps every engine's slots in the title when the list is truncated (#497)", () => {
    render(
      <MemoryRouter>
        <NowStrip
          pauses={[]}
          activeRun={null}
          runningJobs={0}
          engines={[engine("native-datafusion", 0, 5), engine("spark-k8s-fallback", 3, 30), engine("trino", 1, 4)]}
          enginesError={false}
          workersOnline={2}
          offPeak={null}
          scannerLabel="Idle"
          queueDepth={0}
          lastScanAgo={null}
          scanStale={false}
        />
      </MemoryRouter>
    );
    expect(screen.getByTitle("native-datafusion 0/5 · spark-k8s-fallback 3/30 · trino 1/4")).toHaveTextContent("+1");
  });
});
