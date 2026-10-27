import { X } from "lucide-react";
import { useEffect, type ReactNode } from "react";

export default function Modal({
  children,
  close,
  label,
  wide = false,
}: {
  children: ReactNode;
  close: () => void;
  label: string;
  wide?: boolean;
}) {
  useEffect(() => {
    const before = document.activeElement as HTMLElement;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("keydown", handler);
    return () => {
      document.removeEventListener("keydown", handler);
      before?.focus();
    };
  }, [close]);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <section
        className={`modal ${wide ? "wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={label}
      >
        <button
          className="icon-button modal-close"
          onClick={close}
          aria-label="Закрыть"
        >
          <X size={19} />
        </button>
        {children}
      </section>
    </div>
  );
}
