import { Button } from "../ui/button";

interface SimpleTablePaginationProps {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
}

export const SimpleTablePagination = ({
  page,
  pageSize,
  total,
  onPageChange
}: SimpleTablePaginationProps) => {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  if (total <= pageSize) return null;

  const rangeStart = page * pageSize + 1;
  const rangeEnd = Math.min(total, (page + 1) * pageSize);

  return (
    <div className="td-schema-table-pagination">
      <span>
        {rangeStart}–{rangeEnd} of {total}
      </span>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        disabled={page === 0}
        onClick={() => onPageChange(page - 1)}
      >
        Previous
      </Button>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        disabled={page >= pageCount - 1}
        onClick={() => onPageChange(page + 1)}
      >
        Next
      </Button>
    </div>
  );
};
