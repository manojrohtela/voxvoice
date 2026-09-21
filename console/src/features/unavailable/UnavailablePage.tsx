import { EmptyState } from "../../components/EmptyState";
import type { NavItem } from "../../app/navigation";

/** The honest surface for a product area the server does not declare yet. No sample data. */
export function UnavailablePage({ item }: { item: NavItem }) {
  return (
    <section className="page">
      <header className="page__header">
        <h1>{item.label}</h1>
        <p className="page__lede">{item.summary}</p>
      </header>
      <EmptyState title={`Not yet available — arrives in ${item.milestone}`}>
        <p>
          This server does not declare the <code>{item.capability}</code> capability yet, so the
          console shows nothing here rather than placeholder data.
        </p>
      </EmptyState>
    </section>
  );
}
