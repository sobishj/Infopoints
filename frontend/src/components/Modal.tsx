import { ReactNode, useEffect, useRef } from "react";

export function Modal({ title, children, onClose, wide }: {
  title: string; children: ReactNode; onClose: () => void; wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); prev?.focus(); };
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/20 px-4" onMouseDown={onClose}>
      <div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label={title}
           className={`card w-full ${wide ? "max-w-2xl" : "max-w-md"} p-6 outline-none`}
           onMouseDown={(e) => e.stopPropagation()}>
        <h2 className="mb-3 text-[17px]">{title}</h2>
        {children}
      </div>
    </div>
  );
}

export function Confirm({ title, message, confirmLabel, danger, onConfirm, onCancel }: {
  title: string; message: ReactNode; confirmLabel: string; danger?: boolean;
  onConfirm: () => void; onCancel: () => void;
}) {
  return (
    <Modal title={title} onClose={onCancel}>
      <div className="text-[15px] text-ink">{message}</div>
      <div className="mt-6 flex justify-end gap-2">
        <button className="btn-secondary" onClick={onCancel}>Cancel</button>
        <button className={danger ? "btn-danger" : "btn-primary"} onClick={onConfirm} autoFocus>{confirmLabel}</button>
      </div>
    </Modal>
  );
}
