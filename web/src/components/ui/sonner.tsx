import { Toaster as Sonner } from "sonner";

import { useTheme } from "../../lib/themeContext";

export const Toaster = () => {
  const { theme } = useTheme();

  return (
    <Sonner
      theme={theme}
      position="top-right"
      closeButton
      richColors
      toastOptions={{
        classNames: {
          toast: "font-sans text-sm",
          title: "text-foreground",
          description: "text-muted-foreground"
        }
      }}
    />
  );
};
