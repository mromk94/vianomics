# Import every model so Alembic autogenerate and create_all see the
# complete metadata graph.
from app.models.agents import AgentDef, AgentOutput, AgentRun, EvidenceRef
from app.models.dossier import (
    Dossier,
    DossierReview,
    DossierSection,
    EvidenceItem,
)
from app.models.fundamentals import FundamentalObservation, FinancialStatement
from app.models.backtest import BacktestRun
from app.models.execution import BrokerOrderRec, ExecutionEvent, KillSwitch
from app.models.governance import (
    Approval, DecisionRecord, ExitSignal, OrderTicket, RiskAssessment,
)
from app.models.identity import AuditEvent, Role, Session, User, user_roles
from app.models.macro import RegimeRun
from app.models.instruments import (
    CorporateAction,
    Exchange,
    Industry,
    Instrument,
    InstrumentIdentifier,
    Sector,
)
from app.models.mandate import Mandate
from app.models.market import (
    EconomicRelease,
    MacroObservation,
    MacroSeries,
    OhlcvBar,
)
from app.models.ops import Alert, Job, JobRun, QuarantinedRecord, SyncStatus
from app.models.portfolio import LedgerEntry, Portfolio, Position, Trade
from app.models.providers import DataProvider, ProviderCredentialMeta
from app.models.research import AnalysisRun
from app.models.risk import PyramidTradeRec, RiskCheck
from app.models.screening import (
    ScreeningPolicy,
    ScreeningResult,
    ScreeningRun,
)
from app.models.universe import (
    Universe,
    UniverseMembership,
    Watchlist,
    WatchlistItem,
)
from app.models.valuation import ValuationRun

__all__ = [n for n in dir() if not n.startswith("_")]
