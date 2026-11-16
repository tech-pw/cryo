import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle
} from "./ui/alert-dialog";

export const DiscardChangesDialog = ({
  open,
  onDiscard,
  onCancel
}: {
  open: boolean;
  onDiscard: () => void;
  onCancel: () => void;
}) => (
  <AlertDialog open={open} onOpenChange={(next) => (next ? null : onCancel())}>
    <AlertDialogContent>
      <AlertDialogHeader>
        <AlertDialogTitle>Discard unsaved changes?</AlertDialogTitle>
        <AlertDialogDescription>
          This preset has edits that are not saved. Leaving now discards them.
        </AlertDialogDescription>
      </AlertDialogHeader>
      <AlertDialogFooter>
        <AlertDialogCancel onClick={onCancel}>Keep editing</AlertDialogCancel>
        <AlertDialogAction onClick={onDiscard}>Discard changes</AlertDialogAction>
      </AlertDialogFooter>
    </AlertDialogContent>
  </AlertDialog>
);
