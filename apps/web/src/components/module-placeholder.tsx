import { findModule } from "@/lib/modules";

import { EmptyState } from "./ui/empty-state";
import { PageHeader } from "./ui/page-header";
import { SectionCard } from "./ui/section-card";
import { StatusBadge } from "./ui/status-badge";

/** Honest placeholder for modules whose engines haven't landed yet.
 *  Shows which VAIIP parts the module will implement and which roadmap
 *  milestone delivers it — no fabricated data. */
export function ModulePlaceholder({ slug }: { slug: string }) {
  const mod = findModule(slug);
  if (!mod) return null;
  const Icon = mod.icon;
  return (
    <div>
      <PageHeader
        title={mod.label}
        description={mod.description}
        meta={<StatusBadge tone="info">Pending · {mod.milestone}</StatusBadge>}
      />
      <SectionCard>
        <EmptyState
          icon={Icon}
          title="Engine not yet implemented"
          hint={`This module implements VAIIP ${
            mod.parts.length === 1 ? "part" : "parts"
          } ${mod.parts.join(", ")} and is scheduled for milestone ${
            mod.milestone
          }. No data is shown because nothing has been fabricated.`}
        />
      </SectionCard>
    </div>
  );
}
