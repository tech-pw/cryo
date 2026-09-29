import { forwardRef } from "react";
import { Button } from "../ui/button";
import { Textarea } from "../ui/textarea";

export const Composer = forwardRef<
  HTMLTextAreaElement,
  {
    value: string;
    onChange: (value: string) => void;
    onSend: () => void;
    onStop: () => void;
    streaming: boolean;
    disabled: boolean;
    placeholder: string;
  }
>(({ value, onChange, onSend, onStop, streaming, disabled, placeholder }, ref) => (
  <form
    className="space-y-2 border-t border-border p-3"
    onSubmit={(event) => {
      event.preventDefault();
      if (!streaming) onSend();
    }}
  >
    <label className="sr-only" htmlFor="ask-cryo-composer">
      Ask Cryo
    </label>
    <Textarea
      ref={ref}
      id="ask-cryo-composer"
      rows={3}
      value={value}
      disabled={disabled}
      placeholder={placeholder}
      className="min-h-[72px]"
      onChange={(event) => onChange(event.target.value)}
      onKeyDown={(event) => {
        if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
          event.preventDefault();
          if (!streaming && !disabled) onSend();
        }
      }}
    />
    <div className="flex items-center justify-between gap-2">
      <p className="text-xs text-muted-foreground">Enter to send · Shift+Enter for a new line</p>
      {streaming ? (
        <Button type="button" variant="secondary" size="sm" onClick={onStop}>
          Stop
        </Button>
      ) : (
        <Button type="submit" variant="primary" size="sm" disabled={disabled || !value.trim()}>
          Send
        </Button>
      )}
    </div>
  </form>
));
Composer.displayName = "Composer";
