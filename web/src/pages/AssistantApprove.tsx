import { useParams } from "react-router-dom";
import { ActionReviewCard } from "../components/assistant/actions/ActionReviewCard";
import { Alert } from "../components/ui/alert";
import { PageHeader } from "../components/ui/page-header";

export const AssistantApprove = () => {
  const { proposalId } = useParams();
  if (!proposalId) return <Alert variant="error" title="Missing proposal" />;
  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <PageHeader
        title="Review action"
        subtitle="Authorize only the plan on this card. A typed yes is not a confirmation."
      />
      <ActionReviewCard proposalId={proposalId} />
    </div>
  );
};
