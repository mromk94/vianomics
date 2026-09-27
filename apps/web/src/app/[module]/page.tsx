import { notFound } from "next/navigation";

import { MacroPage } from "@/components/macro-page";
import { MandatePage } from "@/components/mandate-page";
import { ModulePlaceholder } from "@/components/module-placeholder";
import { QuantPage } from "@/components/quant-page";
import { ResearchPage } from "@/components/research-page";
import { RiskPage } from "@/components/risk-page";
import { ScreenerPage } from "@/components/screener-page";
import { TradingPage } from "@/components/trading-page";
import { UniversePage } from "@/components/universe-page";
import { ValuationPage } from "@/components/valuation-page";
import { findModule } from "@/lib/modules";

import { AllocationPage } from "@/components/allocation-page";
import { DataOpsPage } from "@/components/dataops-page";
import { DocsPage } from "@/components/docs-page";
import { MarketPage } from "@/components/market-page";
import { PortfolioPage } from "@/components/portfolio-page";
import { TechnicalPage } from "@/components/technical-page";
import { CommitteePage } from "@/components/committee-page";
import { BacktestPage } from "@/components/backtest-page";
import { JournalPage } from "@/components/journal-page";
import { MonitoringPage } from "@/components/monitoring-page";

const REAL_PAGES: Record<string, React.ComponentType> = {
  universe: UniversePage,
  screener: ScreenerPage,
  research: ResearchPage,
  valuation: ValuationPage,
  quant: QuantPage,
  macro: MacroPage,
  "trading-desk": TradingPage,
  risk: RiskPage,
  committee: CommitteePage,
  journal: JournalPage,
  monitoring: MonitoringPage,
  backtesting: BacktestPage,
  market: MarketPage,
  technical: TechnicalPage,
  portfolio: PortfolioPage,
  allocation: AllocationPage,
  "data-ops": DataOpsPage,
  docs: DocsPage,
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
