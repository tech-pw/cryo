import { Badge } from "../../ui/badge";

export const VerifiedBadge = ({ handler }: { handler?: string | null }) => (
  <Badge variant="success" title={handler ?? undefined}>
    Verified
  </Badge>
);
