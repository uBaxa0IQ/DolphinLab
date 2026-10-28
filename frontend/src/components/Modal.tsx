import { X } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";

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
  const panel = useRef<HTMLElement>(null);
  const opener = useRef(document.activeElement as HTMLElement | null);
  const onClose = useRef(close);
  onClose.current = close;
  useEffect(() => {
    const focusable = () =>
      Array.from(
        panel.current?.querySelectorAll<HTMLElement>(
          'button:not(:disabled), a[href], input:not(:disabled):not([type="hidden"]), textarea:not(:disabled), select:not(:disabled), [tabindex="0"]',
        ) || [],
      ).filter((el) => el.getClientRects().length > 0);
    if (!panel.current?.contains(document.activeElement)) {
      (
        panel.current?.querySelector<HTMLElement>(
          'input:not([type="hidden"]), textarea, select',
        ) ||
        focusable()[0] ||
        panel.current
      )?.focus();
    }
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose.current();
      }
      if (e.key === "Tab") {
        const targets = focusable(),
          first = targets[0],
          last = targets.at(-1);
        if (!first) {
          e.preventDefault();
          panel.current?.focus();
        } else if (
          e.shiftKey &&
          (document.activeElement === first ||
            !panel.current?.contains(document.activeElement))
        ) {
          e.preventDefault();
          last?.focus();
        } else if (
          !e.shiftKey &&
          (document.activeElement === last ||
            !panel.current?.contains(document.activeElement))
        ) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", handler);
    return () => {
      document.removeEventListener("keydown", handler);
      opener.current?.focus();
    };
  }, []);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <section
        ref={panel}
        tabIndex={-1}
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
