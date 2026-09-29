"use client";

import { useEffect, useState } from "react";
import { ChevronDown, FileText, GitBranch, ShieldAlert } from "lucide-react";

import { apiGet, apiPost, ApiError, getToken } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { Sym } from "@/components/symbol-drawer";
import { SectionCard } from "@/components/ui/section-card";
import { SearchInput } from "@/components/ui/search-input";
import { SkeletonRows } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/ui/status-badge";

interface Claim {
  kind: string;
  claim_type: "verified_fact" | "ai_inference" | "analyst_assumption" | "management_statement" | "insufficient_data";
  text: string;
  evidence_ids: string[];
}

interface Section {
  section_no: number;
  title: string;
  status: "verified" | "partial" | "insufficient";
  content: Claim[];
}

interface Evidence {
  kind: string;
  concept: string | null;
  value: string | null;
  unit: string | null;
  period_end: string | null;
  source: string | null;
}

interface Dossier {
  id: string;
  version: number;
  status: string;
  symbol: string;
  name: string;
  green_zone_score: number | null;
  mandate_version: number | null;
  created_at: string | null;
  missing_evidence: { item: string; reason: string }[];
  sections: Section[];
  evidence: Record<string, Evidence>;
}

interface SearchRow { symbol: string; name: string; }

const CLAIM_STYLE: Record<Claim["claim_type"], { label: string; cls: string }> = {
  verified_fact: { label: "VERIFIED", cls: "text-pos border-pos/30 bg-pos/5" },
  ai_inference: { label: "AI", cls: "text-info border-info/30 bg-info/5" },
  analyst_assumption: { label: "ASSUMPTION", cls: "text-warn border-warn/30 bg-warn/5" },
  management_statement: { label: "MGMT", cls: "text-accent border-accent/30 bg-accent-soft" },
  insufficient_data: { label: "MISSING", cls: "text-faint border-border bg-surface-2" },
};

const SECTION_TONE: Record<string, "pos" | "warn" | "info"> = {
  verified: "pos",
  partial: "warn",
  insufficient: "info",
};

export function ResearchPage() {
  const [query, setQuery] = useState("");
  const [options, setOptions] = useState<SearchRow[]>([]);
  const [symbol, setSymbol] = useState<string | null>(null);
  const [dossier, setDossier] = useState<Dossier | null>(null);
  const [versions, setVersions] = useState<{ id: string; version: number; status: string; created_at: string }[]>([]);
  const [open, setOpen] = useState<number | null>(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!query) { setOptions([]); return; }
    const t = setTimeout(() => {
      apiGet<SearchRow[]>(`/api/v1/universe/instruments?q=${encodeURIComponent(query)}&limit=8`)
        .then(setOptions).catch(() => {});
    }, 250);
    return () => clearTimeout(t);
  }, [query]);

  const loadDossier = (sym: string) => {
    setLoading(true);
    setError(null);
    apiGet<Dossier>(`/api/v1/research/dossier/${sym}`)
      .then((d) => {
        setDossier(d);
        apiGet<typeof versions>(`/api/v1/research/dossier/${sym}/versions`)
          .then(setVersions).catch(() => {});
      })
      .catch((e) => {
        setDossier(null);
        if (e instanceof ApiError && e.status === 404) {
          setNotice(`No dossier for ${sym} yet — generate one.`);
        } else setError(e.message);
      })
      .finally(() => setLoading(false));
  };

  const generate = async (sym: string) => {
    setNotice(null);
    try {
      await apiPost(`/api/v1/research/dossier/${sym}`, {});
      loadDossier(sym);
    } catch (e) {
      setNotice(
        e instanceof ApiError && e.status === 401
          ? "Sign in (Settings) with a research role to generate dossiers."
          : (e as Error).message,
      );
    }
  };

  const review = async (verdict: string) => {
    if (!dossier) return;
    try {
      await apiPost(`/api/v1/research/dossier/${dossier.symbol}/review`,
        { verdict, note: null });
      loadDossier(dossier.symbol);
    } catch (e) {
      setNotice((e as Error).message);
    }
  };

  const ev = dossier?.evidence ?? {};

  return (
    <div className="space-y-4">
      <PageHeader
        title="Research Workbench"
        subtitle="18-section institutional dossier — evidence-cited, versioned"
        actions={symbol && (
          <button
            onClick={() => generate(symbol)}
            className="rounded-full bg-accent px-4 py-1.5 text-[13px] font-semibold text-[#0b0f1a] transition hover:brightness-110"
          >
            Generate dossier
          </button>
        )}
      />
      {notice && <div className="glass border-warn/40 p-3 text-[13px] text-warn">{notice}</div>}
      {error && <ErrorState title="Cannot reach the VAIIP API" detail={error} />}

      <SectionCard title="Security" className="rise">
        <div className="relative max-w-sm">
          <SearchInput value={query} onChange={setQuery} placeholder="Search ticker…" />
          {options.length > 0 && (
            <div className="glass absolute z-20 mt-1 w-full overflow-hidden rounded-xl border border-border">
              {options.map((o) => (
                <button
                  key={o.symbol}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] hover:bg-surface-2"
                  onClick={() => { setSymbol(o.symbol); setQuery(o.symbol); setOptions([]); loadDossier(o.symbol); }}
                >
                  <Sym s={o.symbol} />
                  <span className="truncate text-dim">{o.name}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </SectionCard>

      {loading && <SkeletonRows />}

      {dossier && !loading && (
        <>
          {/* header strip */}
          <div className="rise glass flex flex-wrap items-center gap-x-5 gap-y-1 p-4 text-[12px]">
            <span className="text-base font-semibold">{dossier.symbol}</span>
            <StatusBadge tone={dossier.status === "final" ? "pos" : dossier.status === "review" ? "warn" : "info"}>
              v{dossier.version} · {dossier.status}
            </StatusBadge>
            {dossier.green_zone_score !== null && (
              <span className="text-dim">Green Zone <strong className="num text-text">{dossier.green_zone_score}</strong></span>
            )}
            <span className="text-faint">mandate v{dossier.mandate_version ?? "—"} · {fmtTime(dossier.created_at)}</span>
            {getToken() && dossier.status !== "final" && (
              <span className="ml-auto flex gap-2">
                <button onClick={() => review("approve")}
                  className="rounded-full bg-pos/15 px-3 py-1 text-[12px] font-medium text-pos hover:bg-pos/25">
                  Approve → final
                </button>
                <button onClick={() => review("request_changes")}
                  className="rounded-full bg-warn/15 px-3 py-1 text-[12px] font-medium text-warn hover:bg-warn/25">
                  Request changes
                </button>
              </span>
            )}
          </div>

          {/* legend + missing evidence */}
          <div className="rise rise-1 grid gap-3 lg:grid-cols-[1fr_320px]">
            <div className="glass p-3.5 text-[11px]">
              <div className="mb-2 text-[10px] tracking-wider text-dim uppercase">Claim provenance</div>
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(CLAIM_STYLE).map(([k, s]) => (
                  <span key={k} className={`rounded-full border px-2 py-0.5 ${s.cls}`}>{s.label}</span>
                ))}
              </div>
            </div>
            <div className="glass border-warn/30 p-3.5">
              <div className="mb-1.5 flex items-center gap-1.5 text-[10px] tracking-wider text-warn uppercase">
                <ShieldAlert className="size-3" /> Missing evidence ({dossier.missing_evidence.length})
              </div>
              {dossier.missing_evidence.length === 0 ? (
                <span className="text-[12px] text-faint">None</span>
              ) : (
                <ul className="max-h-28 space-y-0.5 overflow-y-auto text-[11px] text-dim">
                  {dossier.missing_evidence.slice(0, 12).map((m, i) => (
                    <li key={i}>· {m.item}</li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          {/* 18 sections */}
          <SectionCard title="Research Dossier — 18 sections" className="rise rise-2">
            <div className="space-y-1.5">
              {dossier.sections.map((s) => (
                <div key={s.section_no} className="overflow-hidden rounded-lg border border-border">
                  <button
                    onClick={() => setOpen(open === s.section_no ? null : s.section_no)}
                    className="flex w-full items-center justify-between px-3.5 py-2.5 text-left hover:bg-surface-2/50"
                  >
                    <span className="flex items-center gap-2.5 text-[13px] font-medium">
                      <span className="num w-5 text-faint">{s.section_no}</span>
                      {s.title}
                    </span>
                    <span className="flex items-center gap-2">
                      <StatusBadge tone={SECTION_TONE[s.status] ?? "neutral"}>{s.status}</StatusBadge>
                      <ChevronDown className={`size-4 text-dim transition-transform ${open === s.section_no ? "rotate-180" : ""}`} />
                    </span>
                  </button>
                  {open === s.section_no && (
                    <div className="space-y-2 border-t border-border bg-surface-2/30 px-4 py-3">
                      {s.content.map((c, i) => (
                        <div key={i} className="flex gap-2.5">
                          <span className={`mt-0.5 h-fit shrink-0 rounded-full border px-1.5 py-px text-[9px] font-semibold tracking-wider ${CLAIM_STYLE[c.claim_type]?.cls}`}>
                            {CLAIM_STYLE[c.claim_type]?.label}
                          </span>
                          <div className="text-[13px] leading-relaxed text-text/90">
                            {c.text}
                            {c.evidence_ids.length > 0 && (
                              <div className="mt-1 flex flex-wrap gap-1">
                                {c.evidence_ids.map((eid) => {
                                  const e = ev[eid];
                                  return e ? (
                                    <span key={eid} title={`${e.concept} = ${e.value} (${e.period_end ?? "instant"}) via ${e.source}`}
                                      className="cursor-help rounded bg-accent-soft px-1.5 py-px text-[10px] text-accent">
                                      <FileText className="mr-0.5 inline size-2.5" />
                                      {e.concept?.split(":")[1]} · {e.period_end ?? "latest"}
                                    </span>
                                  ) : null;
                                })}
                              </div>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </SectionCard>

          {/* versions */}
          {versions.length > 0 && (
            <SectionCard title="Versions" className="rise rise-3"
              action={<GitBranch className="size-4 text-dim" />}>
              <ul className="space-y-1 text-[13px]">
                {versions.map((v) => (
                  <li key={v.id} className="glass-tile flex items-center justify-between px-3.5 py-2">
                    <span className="font-medium">v{v.version}</span>
                    <StatusBadge tone={v.status === "final" ? "pos" : "info"}>{v.status}</StatusBadge>
                    <span className="num text-[12px] text-faint">{fmtTime(v.created_at)}</span>
                  </li>
                ))}
              </ul>
            </SectionCard>
          )}
        </>
      )}

      {!dossier && !loading && !symbol && (
        <EmptyState title="Select a security" hint="Search an approved-universe ticker to view or generate its research dossier." />
      )}
    </div>
  );
}
