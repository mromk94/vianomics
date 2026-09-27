import { notFound } from "next/navigation";

import { MacroPage } from "@/components/macro-page";
import { MandatePage } from "@/components/mandate-page";
import { ModulePlaceholder } from "@/components/module-placeholder";
import { QuantPage } from "@/components/quant-page";
import { ResearchPage } from "@/components/research-page";
import { ScreenerPage } from "@/components/screener-page";
import { TradingPage } from "@/components/trading-page";
import { UniversePage } from "@/components/universe-page";
import { ValuationPage } from "@/components/valuation-page";
import { findModule } from "@/lib/modules";

const REAL_PAGES: Record<string, React.ComponentType> = {
  universe: UniversePage,
  screener: ScreenerPage,
  research: ResearchPage,
  valuation: ValuationPage,
  quant: QuantPage,
  macro: MacroPage,
  "trading-desk": TradingPage,
  settings: MandatePage,
};

export default async function ModulePage({
  params,
}: {
  params: Promise<{ module: string }>;
}) {
  const { module: slug } = await params;
  const mod = findModule(slug);
  if (!mod || mod.slug === "") notFound();
  const Page = REAL_PAGES[slug];
  return Page ? <Page /> : <ModulePlaceholder slug={slug} />;
}

export async function generateStaticParams() {
  return [];
}
