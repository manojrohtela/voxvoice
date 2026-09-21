import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  failed: boolean;
}

/** Contains a render failure to the current view; the shell and navigation stay usable. */
export class ErrorBoundary extends Component<Props, State> {
  override state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Console view failed to render", error, info.componentStack);
  }

  override render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="state state--error" role="alert">
        <p className="state__title">This view failed to load</p>
        <p className="state__body">The rest of the console still works. Reload the page to retry.</p>
      </div>
    );
  }
}
