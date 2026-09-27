import { ReactNode } from "react";

export function SectionCard({
  title,
  action,
  demo = false,
  className = "",
  id,
  children,
}: {
  title?: string;
  action?: ReactNode;
  demo?: boolean;
  className?: string;
  id?: string;
  children: ReactNode;
}) {
  return (
    <section id={id} className={`glass p-5 ${className}`}>
      {(title || action) && (
        <header className="mb-4 flex items-center justify-between gap-3">
          <h2 className="text-[13px] font-semibold tracking-wider text-dim uppercase">
            {title}
            {demo && (
              <span className="ml-2 rounded-full border border-warn/40 bg-warn-bg px-2 py-px align-middle text-[10px] font-bold tracking-wider text-warn">
                DEMO
              </span>
            )}
          </h2>
          {action && (
            <span className="text-[12px] text-dim">{action}</span>
          )}
        </header>
      )}
      {children}
    </section>
  );
}
