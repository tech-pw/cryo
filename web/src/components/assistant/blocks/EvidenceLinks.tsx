import { useQuery } from "@tanstack/react-query";
import { getAssistantEvidence, formatAssistantCell } from "../../../api/assistant";
import { useAuth } from "../../../lib/auth";
import { formatTime } from "../../../lib/format";

const EvidenceLink = ({ id }: { id: string }) => {
  const { api, token } = useAuth();
  const evidence = useQuery({
    queryKey: ["assistant", "evidence", id, token],
    queryFn: () => getAssistantEvidence(api, id),
    enabled: false,
    retry: false
  });

  return (
    <li className="text-xs">
      <button
        type="button"
        className="text-primary underline-offset-2 hover:underline"
        onClick={() => void evidence.refetch()}
      >
        Evidence {id.slice(0, 8)}
      </button>
      {evidence.data ? (
        <span className="ml-2 text-muted-foreground">
          {formatAssistantCell(evidence.data.classification)} · {formatTime(evidence.data.observed_at)}
        </span>
      ) : null}
      {evidence.isError ? <span className="ml-2 text-muted-foreground">Unavailable</span> : null}
    </li>
  );
};

export const EvidenceLinks = ({ ids }: { ids?: string[] }) => {
  if (!ids?.length) return null;
  return (
    <ul className="mt-2 space-y-1" aria-label="Evidence">
      {ids.map((id) => (
        <EvidenceLink key={id} id={id} />
      ))}
    </ul>
  );
};
