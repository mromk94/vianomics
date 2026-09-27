import {
  Activity,
  ArrowLeftRight,
  Bell,
  BookOpen,
  BrainCircuit,
  Briefcase,
  Calculator,
  Database,
  FlaskConical,
  Globe,
  LayoutDashboard,
  LineChart,
  ListChecks,
  PieChart,
  Scale,
  Settings,
  ShieldAlert,
  Target,
  TrendingUp,
  type LucideIcon,
} from "lucide-react";

export interface NavModule {
  slug: string;
  label: string;
  icon: LucideIcon;
  /** VAIIP framework parts this module implements */
  parts: string[];
  /** Roadmap milestone delivering the real engine */
  milestone: string;
  description: string;
}

export interface NavGroup {
  label: string;
  modules: NavModule[];
}

export const NAV: NavGroup[] = [
  {
    label: "Overview",
    modules: [
      {
        slug: "",
        label: "Command Center",
        icon: LayoutDashboard,
        parts: ["P25", "P33"],
        milestone: "M6–M7",
        description:
          "Portfolio state, regime, risk utilization, approvals and alerts at a glance.",
      },
    ],
  },
  {
    label: "Markets",
    modules: [
      {
        slug: "market",
        label: "Market Overview",
        icon: Globe,
        parts: ["P3"],
        milestone: "M1",
        description: "Indices, sectors, breadth and market-level data feeds.",
      },
      {
        slug: "macro",
        label: "Macro Regime",
        icon: Activity,
        parts: ["P9", "P11"],
        milestone: "M3",
        description:
          "Economic & market regime classification and sector rotation overlay.",
      },
      {
        slug: "technical",
        label: "Technical Timing",
        icon: LineChart,
        parts: ["P10"],
        milestone: "M3",
        description:
          "Mean-reversion and trend-following entry signals across timeframes.",
      },
    ],
  },
  {
    label: "Research",
    modules: [
      {
        slug: "universe",
        label: "Investment Universe",
        icon: ListChecks,
        parts: ["P2"],
        milestone: "M1",
        description:
          "Hierarchical universe management: global → eligible → approved.",
      },
      {
        slug: "screener",
        label: "Green Zone Screener",
        icon: Target,
        parts: ["P4", "P5"],
        milestone: "M3",
        description:
          "20-criteria quantitative screen with sector-adjusted valuation checks.",
      },
      {
        slug: "research",
        label: "Research Workbench",
        icon: BookOpen,
        parts: ["P6"],
        milestone: "M6",
        description:
          "18-section institutional research dossiers and peer comparison.",
      },
      {
        slug: "valuation",
        label: "Valuation Lab",
        icon: Calculator,
        parts: ["P5", "P7"],
        milestone: "M3",
        description:
          "DCF, reverse DCF, relative valuation and Rule #1 sticker prices.",
      },
      {
        slug: "quant",
        label: "Quantitative Intelligence",
        icon: FlaskConical,
        parts: ["P8"],
        milestone: "M3–M8",
        description:
          "Factor exposure, event studies, walk-forward and Monte Carlo.",
      },
      {
        slug: "backtesting",
        label: "Backtesting Lab",
        icon: TrendingUp,
        parts: ["P31", "P32"],
        milestone: "M8",
        description:
          "Bias-controlled strategy validation and feedback/attribution.",
      },
    ],
  },
  {
    label: "Portfolio",
    modules: [
      {
        slug: "trading-desk",
        label: "Trading Desk",
        icon: ArrowLeftRight,
        parts: ["P12", "P30"],
        milestone: "M4–M7",
        description:
          "ATR pyramid state machine, trade construction and execution queue.",
      },
      {
        slug: "portfolio",
        label: "Investment Portfolio",
        icon: Briefcase,
        parts: ["P13"],
        milestone: "M4",
        description:
          "Holdings, 5-test monitoring and position lifecycle.",
      },
      {
        slug: "allocation",
        label: "Portfolio Allocation",
        icon: PieChart,
        parts: ["P17", "P18"],
        milestone: "M6",
        description:
          "Capital allocation vs marginal holdings, sector exposure, rebalancing.",
      },
    ],
  },
  {
    label: "Governance",
    modules: [
      {
        slug: "risk",
        label: "Risk Center",
        icon: ShieldAlert,
        parts: ["P14", "P15", "P16"],
        milestone: "M4–M6",
        description:
          "7-dimension risk calculations, hard limits and the Risk Manager veto.",
      },
      {
        slug: "committee",
        label: "AI Investment Committee",
        icon: BrainCircuit,
        parts: ["P19", "P20", "P21", "P22", "P26", "P27", "P28"],
        milestone: "M5–M6",
        description:
          "Agent consensus, CIO synthesis, conflict resolution and decisions tree.",
      },
      {
        slug: "monitoring",
        label: "Monitoring & Alerts",
        icon: Bell,
        parts: ["P25"],
        milestone: "M7",
        description:
          "Continuous company, valuation, macro, portfolio and pyramid monitoring.",
      },
      {
        slug: "journal",
        label: "Decision Journal",
        icon: Scale,
        parts: ["P33", "P34"],
        milestone: "M6",
        description:
          "Immutable investment decision records and the Ten Questions report.",
      },
    ],
  },
  {
    label: "System",
    modules: [
      {
        slug: "data-ops",
        label: "Data Operations",
        icon: Database,
        parts: ["P3"],
        milestone: "M1",
        description:
          "Provider health, ingestion jobs, freshness and provenance.",
      },
      {
        slug: "docs",
        label: "Docs & Help",
        icon: BookOpen,
        parts: [],
        milestone: "—",
        description:
          "How every feature works, where data comes from, and how to operate the platform.",
      },
      {
        slug: "settings",
        label: "Settings & Mandate",
        icon: Settings,
        parts: ["P1", "P15"],
        milestone: "M1",
        description:
          "Investment mandate, objectives, risk limits and platform configuration.",
      },
    ],
  },
];

export const ALL_MODULES: NavModule[] = NAV.flatMap((g) => g.modules);

export function findModule(slug: string): NavModule | undefined {
  return ALL_MODULES.find((m) => m.slug === slug);
}
