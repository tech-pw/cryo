import { Component, type ErrorInfo, type ReactNode } from "react";
import { Alert } from "./ui/alert";
import { Button } from "./ui/button";

interface ErrorBoundaryProps {
  children: ReactNode;
  fallback?: ReactNode;
  onError?: (error: Error, info: ErrorInfo) => void;
}
interface ErrorBoundaryState {
  error: Error | null;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    this.props.onError?.(error, info);
  }

  reset = () => this.setState({ error: null });

  render() {
    if (this.state.error) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <div className="p-6" role="alert">
          <Alert
            variant="error"
            title="Something went wrong rendering this view."
            description={
              <>
                <pre className="mb-3 whitespace-pre-wrap">{this.state.error.message}</pre>
                <Button size="sm" onClick={this.reset}>
                  Try again
                </Button>
              </>
            }
          />
        </div>
      );
    }
    return this.props.children;
  }
}
