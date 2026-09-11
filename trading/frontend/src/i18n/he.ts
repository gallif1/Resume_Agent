/** מחרוזות ממשק בעברית למערכת המסחר. */

export const he = {
  brandKicker: "מסחר נייר חי",
  brandTitle: "מערכת מסחר AI",
  backLink: "→ Resume Agent",

  start: "התחל",
  pause: "השהה",
  stop: "עצור",
  clearLogs: "נקה יומנים",
  resetPaper: "אפס נייר",
  realData: "נתוני שוק אמיתיים",
  simData: "נתונים מדומים",
  ticks: "טיקים",

  stateRunning: "פועל",
  statePaused: "מושהה",
  stateStopped: "עצור",

  wsLive: "WS מחובר",
  wsPolling: "WS מת · סקירה",
  wsConnecting: "WS מתחבר",
  wsDead: "WS מת",

  marketFeed: "פיד שוק",
  symbol: "סימול",
  price: "מחיר",
  change: "שינוי",
  volume: "נפח",
  session: "סשן",
  provider: "ספק",
  waitingMarket: "ממתין לנתוני שוק…",

  portfolio: "תיק",
  cash: "מזומן",
  realizedPnl: "רווח/הפסד ממומש",
  positions: "פוזיציות",
  noPositions: "אין פוזיציות פתוחות עדיין.",

  agents: "סוכנים",
  waitingVote: "ממתין להצבעה ראשונה…",
  aiAnalyst: "אנליסט שוק AI",
  idle: "ממתין",
  off: "כבוי",
  noRationale: "עדיין אין הסבר.",
  aiNoKey: "מפתח AI לא מוגדר — הסוכן לא יופעל.",
  waitingAi: "ממתין לניתוח AI…",
  aiCallsHour: "קריאות AI בשעה זו",
  aiUnlimited: "ללא הגבלה",

  eventEngine: "מנוע אירועים",
  noEvents: "אין אירועים עדיין — לחץ התחל.",

  decisionEngine: "מנוע החלטות",
  copyRecent: "העתק יומנים אחרונים",
  all: "הכל",
  systemPerformance: "ביצועי מערכת",
  momentum: "מומנטום",
  meanReversion: "חזרה לממוצע",
  volatility: "תנודתיות",
  directionalAccuracy: "דיוק כיווני אחרי BUY/SELL…",
  why: "למה?",
  hideDetails: "הסתר פרטים",
  logsAppear: "יומני החלטות מובנים יופיעו אחרי התחל.",

  filled: "בוצע",
  signalActive: "אות פעיל",
  cooldown: "המתנה",
  notFilled: "לא בוצע",

  unavailable: "לא זמין",
  staleData: "נתונים מיושנים",
  marketClosed: "שוק סגור",
  marketOpen: "שוק פתוח",

  copyNoLogs: "אין יומנים עדיין — לחץ התחל קודם",
  copiedN: (n: number) => `הועתקו ${n} יומנים`,
  clipboardBlocked: "לוח גזירים חסום ב-HTTP — סמן הכל למטה והעתק",
  copyFailed: "ההעתקה נכשלה — השתמש בתיבת הטקסט למטה",

  alreadyRunning: "כבר פועל — שינויי מזומן מגיעים מביצועי נייר במנוע ההחלטות.",
  notRunning: "המערכת לא פועלת.",
  alreadyStopped: "כבר עצורה.",
  clearLogsConfirm: "למחוק את יומני ההחלטות האחרונים? התיק והביצועים יישמרו.",
  resetConfirm: "לאפס את מסחר הנייר מאפס? תיק, יומנים וביצועים יימחקו.",

  signal: "אות",
  marketSnapshot: "תמונת שוק",
  decision: "החלטה",
  aiPretrade: "AI לפני עסקה",
  outcome: "תוצאה",
  execution: "ביצוע",

  tabChart: "גרף",
  tabMarket: "שוק",
  tabAgents: "סוכנים",
  tabDecisions: "החלטות",
  tabPortfolio: "תיק",
  moreActions: "עוד",

  expand: "הרחב",
  collapse: "כווץ",
  fullscreen: "מסך מלא",
  exitFullscreen: "צא ממסך מלא",
  indicators: "אינדיקטורים",
  draw: "שרטוט",
  markers: "סמנים",
  goLive: "עבור לחי",
  resetView: "אפס תצוגה",
  agentDecisions: "החלטות סוכנים",
  executedTrades: "עסקאות שבוצעו",
  buyMarkers: "סמני BUY",
  sellMarkers: "סמני SELL",
  holdDecisions: "החלטות HOLD",
  manualDrawings: "שרטוטים ידניים",
  decisionsCount: "החלטות",
  executedCount: "בוצעו",
  loading: "טוען…",
  dataUnavailable: "נתונים לא זמינים",
  markerDetails: "פרטי סמן",
  closeDetails: "סגור פרטים",
  executionTime: "זמן ביצוע",
  candleTime: "זמן נר",
  action: "פעולה",
  quantity: "כמות",
  requestedPrice: "מחיר מבוקש",
  fillPrice: "מחיר ביצוע",
  totalValue: "ערך כולל",
  confidence: "ביטחון",
  agentsVoted: "סוכנים",
  orchestrator: "מנהל אורקסטרציה",
  reasons: "סיבות",
  paperOrderId: "מזהה הזמנת נייר",
  fillId: "מזהה ביצוע",
  status: "סטטוס",
  skipReason: "סיבת דילוג",
  drawings: "שרטוטים",
  legendBuy: "משולש כחול: החלטת BUY של סוכן",
  legendSell: "משולש סגול: החלטת SELL של סוכן",
  legendFillBuy: "חץ ירוק: קניית נייר שבוצעה",
  legendFillSell: "חץ אדום: מכירת נייר שבוצעה",
  legendYellow: "נקודה צהובה: אות אינדיקטור",
  unmappedTrades: (n: number) =>
    `${n} עסקאות לא ניתן למפות לנרות שנטענו`,
  support: "תמיכה",
  resistance: "התנגדות",
  trendLine: "קו מגמה",
  textNote: "הערת טקסט",
  cancelDraw: "בטל שרטוט",
  deleteSelected: "מחק נבחר",
  clearDrawings: "נקה שרטוטים",
  editSelected: "ערוך נבחר",
  notePrompt: "טקסט ההערה",
  labelPrompt: "תווית",
  importancePrompt: "חשיבות (low|medium|high)",
  clearDrawingsConfirm: "למחוק את כל השרטוטים לסימול זה?",
} as const;

export function stateLabel(state: string): string {
  const s = state.toLowerCase();
  if (s === "running") return he.stateRunning;
  if (s === "paused") return he.statePaused;
  return he.stateStopped;
}

export function sessionLabel(cls: string, fallback: string): string {
  if (cls.includes("unavailable")) return he.unavailable;
  if (cls.includes("stale")) return he.staleData;
  if (cls.includes("closed")) return he.marketClosed;
  if (cls.includes("open")) return he.marketOpen;
  return fallback;
}
