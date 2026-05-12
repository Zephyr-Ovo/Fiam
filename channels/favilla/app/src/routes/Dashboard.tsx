import { Activity, Bell, Calendar as CalendarIcon, FileText, Moon, Watch, MapPin, Sparkles, Heart, Inbox } from 'lucide-react';
import { PieChart, Pie, Cell, ResponsiveContainer } from 'recharts';
import { useEffect, useMemo, useState, useCallback, type ReactNode } from 'react';
import { fetchDashboardSummary, type DashboardHistoryDigest, type DashboardSummary } from '../lib/api';
import { syncRingToServer } from '../lib/ring-ble';
import { appConfig } from '../config';

function greeting(): string {
  const h = new Date().getHours();
  if (h >= 5 && h < 12) return 'Morning';
  if (h >= 12 && h < 18) return 'Afternoon';
  if (h >= 18 && h < 22) return 'Evening';
  return 'Good night';
}

const favillaUsage = [
  { day: 'Mon', h: 1.2 }, { day: 'Tue', h: 3.5 }, { day: 'Wed', h: 2.1 },
  { day: 'Thu', h: 4.8 }, { day: 'Fri', h: 3.0 }, { day: 'Sat', h: 1.5 },
  { day: 'Sun', h: 0.8 }
];

const monthlyEmojis: Record<number, string> = {
  3: '✍️',
  5: '🎧',
  12: '✨',
  14: '✍️',
  18: '📚',
  21: '💡',
  25: '✍️',
  28: '🏃'
};

const weekNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

function wordUnits(text?: string) {
  return (text || '').match(/[A-Za-z0-9_]+|[\u4e00-\u9fff]/g)?.length || 0;
}

function dayKey(date: Date) {
  const month = `${date.getMonth() + 1}`.padStart(2, '0');
  const day = `${date.getDate()}`.padStart(2, '0');
  return `${date.getFullYear()}-${month}-${day}`;
}

function totalDigest(summary?: DashboardSummary): DashboardHistoryDigest {
  const chat = summary?.chat;
  const stroll = summary?.stroll;
  const studio = summary?.studio;
  return {
    turns: (chat?.turns || 0) + (stroll?.turns || 0) + (studio?.turns || 0),
    user_turns: (chat?.user_turns || 0) + (stroll?.user_turns || 0) + (studio?.user_turns || 0),
    ai_turns: (chat?.ai_turns || 0) + (stroll?.ai_turns || 0) + (studio?.ai_turns || 0),
    words: (chat?.words || 0) + (stroll?.words || 0) + (studio?.words || 0),
    user_words: (chat?.user_words || 0) + (stroll?.user_words || 0) + (studio?.user_words || 0),
    ai_words: (chat?.ai_words || 0) + (stroll?.ai_words || 0) + (studio?.ai_words || 0),
    by_day: {},
  };
}

function bucketTurns(summary: DashboardSummary | undefined, key: string) {
  return (summary?.chat?.by_day?.[key]?.turns || 0) + (summary?.stroll?.by_day?.[key]?.turns || 0) + (summary?.studio?.by_day?.[key]?.turns || 0);
}

function usageFromSummary(summary?: DashboardSummary) {
  if (!summary) return favillaUsage;
  const today = new Date();
  const days = Array.from({ length: 7 }).map((_, index) => {
    const date = new Date(today);
    date.setDate(today.getDate() - (6 - index));
    return { date, turns: bucketTurns(summary, dayKey(date)) };
  });
  const maxTurns = Math.max(1, ...days.map((day) => day.turns));
  return days.map((day) => ({
    day: weekNames[day.date.getDay()],
    h: day.turns ? Math.max(0.35, (day.turns / maxTurns) * 5) : 0.15,
  }));
}

function usageTurns(summary?: DashboardSummary) {
  if (!summary) return 0;
  const today = new Date();
  let total = 0;
  for (let index = 0; index < 7; index += 1) {
    const date = new Date(today);
    date.setDate(today.getDate() - (6 - index));
    total += bucketTurns(summary, dayKey(date));
  }
  return total;
}

function footprintFromSummary(summary?: DashboardSummary) {
  if (!summary) return [];
  if (summary.locations?.length) return summary.locations.map((item) => ({ name: item.name, words: item.words, percent: item.percent }));
  const strollWords = summary.stroll?.words || 0;
  const chatWords = summary.chat?.words || 0;
  const studioWords = summary.studio?.words || 0;
  const eventWords = (summary.events || []).reduce((sum, event) => sum + wordUnits(event.preview), 0);
  const items = [
    { name: 'Stroll', words: strollWords },
    { name: 'Studio', words: studioWords },
    { name: 'Chat', words: chatWords },
    { name: 'Memory Pool', words: eventWords },
  ].filter((item) => item.words > 0);
  const total = items.reduce((sum, item) => sum + item.words, 0);
  if (!total) return [];
  return items.map((item) => ({ ...item, percent: Math.max(5, Math.round((item.words / total) * 100)) }));
}

function calendarFromSummary(summary?: DashboardSummary) {
  const now = new Date();
  const year = now.getFullYear();
  const month = now.getMonth();
  const firstDay = new Date(year, month, 1).getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const label = new Intl.DateTimeFormat('en', { month: 'long', year: 'numeric' }).format(now);
  const emojis: Record<number, string> = summary ? {} : monthlyEmojis;
  if (summary) {
    Object.entries(summary.chat?.by_day || {}).forEach(([key, bucket]) => {
      const date = new Date(`${key}T00:00:00`);
      if (date.getFullYear() === year && date.getMonth() === month && bucket.turns > 0) {
        emojis[date.getDate()] = bucket.ai_words > bucket.user_words ? '✨' : '✍️';
      }
    });
    Object.entries(summary.stroll?.by_day || {}).forEach(([key, bucket]) => {
      const date = new Date(`${key}T00:00:00`);
      if (date.getFullYear() === year && date.getMonth() === month && bucket.turns > 0) {
        emojis[date.getDate()] = '👣';
      }
    });
    Object.entries(summary.studio?.by_day || {}).forEach(([key, bucket]) => {
      const date = new Date(`${key}T00:00:00`);
      if (date.getFullYear() === year && date.getMonth() === month && bucket.turns > 0) {
        emojis[date.getDate()] = bucket.emoji || (bucket.ai_words > bucket.user_words ? '✨' : '✍️');
      }
    });
  }
  return { daysInMonth, emojis, firstDay, label };
}

function formatStatusLine(summary?: DashboardSummary) {
  if (!summary?.status) return 'Colmi sync active • 84% battery';
  const daemon = summary.status.daemon === 'running' ? 'Fiam online' : 'Fiam offline';
  const events = summary.status.events || 0;
  const beats = summary.status.flow_beats || 0;
  return `${daemon} • ${events.toLocaleString()} events • ${beats.toLocaleString()} beats`;
}

function Card({ children, className = "" }: { children: ReactNode, className?: string }) {
  return (
    <div className={`bg-neutral-50/50 border border-neutral-200/50 rounded-2xl p-4 sm:p-5 ${className}`}>
      {children}
    </div>
  );
}

export function Dashboard({ onBack }: { onBack: () => void }) {
  const [summary, setSummary] = useState<DashboardSummary>();
  const [ringSyncState, setRingSyncState] = useState<'idle' | 'syncing' | 'ok' | 'error'>('idle');

  const refreshSummary = useCallback(() => {
    fetchDashboardSummary().then((result) => {
      if (result.ok) setSummary(result);
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    const load = () => fetchDashboardSummary().then((result) => {
      if (!cancelled && result.ok) setSummary(result);
    });
    load();
    const interval = setInterval(load, 60_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const handleSyncRing = useCallback(async () => {
    if (ringSyncState === 'syncing') return;
    setRingSyncState('syncing');
    const result = await syncRingToServer();
    if (result.ok) {
      setRingSyncState('ok');
      refreshSummary();
      setTimeout(() => setRingSyncState('idle'), 3000);
    } else {
      console.error('[ring-sync] failed:', result.error);
      setRingSyncState('error');
      setTimeout(() => setRingSyncState('idle'), 4000);
    }
  }, [ringSyncState, refreshSummary]);

  const digest = useMemo(() => totalDigest(summary), [summary]);
  const usage = useMemo(() => usageFromSummary(summary), [summary]);
  const usageTotal = useMemo(() => usageTurns(summary), [summary]);
  const footprint = useMemo(() => footprintFromSummary(summary), [summary]);
  const calendar = useMemo(() => calendarFromSummary(summary), [summary]);
  const aiPercent = digest.words ? Math.round((digest.ai_words / digest.words) * 100) : null;
  const coAuthorData = aiPercent != null ? [
    { name: 'Human', value: Math.max(0, 100 - aiPercent), color: '#262626' },
    { name: 'AI Refined', value: aiPercent, color: '#d4d4d4' }
  ] : [{ name: 'Empty', value: 1, color: '#e5e5e5' }];
  const flowBeats = summary?.status?.flow_beats || 0;
  const activityValue = summary?.ring?.steps != null
    ? String(summary.ring.steps)
    : summary ? (flowBeats ? String(flowBeats) : null) : null;
  const currentHr = summary?.ring?.current_hr ?? null;
  const restingHr = summary?.ring?.resting_hr ?? null;
  const pendingTodos = summary?.todos?.length || 0;
  const queueLabel = summary ? (pendingTodos ? String(pendingTodos) : 'Clear') : '--';
  const retryTodos = summary?.health?.retry_todos || 0;

  return (
    <div className="h-full overflow-y-auto bg-neutral-100 text-neutral-900 font-sans selection:bg-neutral-200" style={{paddingTop: 'max(1.5rem, env(safe-area-inset-top))', paddingLeft: '1rem', paddingRight: '1rem', paddingBottom: 'env(safe-area-inset-bottom)'}}>
      <header className="max-w-6xl mx-auto flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-medium tracking-tight">{greeting()}, {appConfig.userName || 'Zephyr'}</h1>
          <p className="text-sm text-neutral-500 mt-1">{formatStatusLine(summary)}</p>
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            aria-label="Sync ring"
            onClick={() => void handleSyncRing()}
            disabled={ringSyncState === 'syncing'}
            title={ringSyncState === 'error' ? 'Sync failed' : ringSyncState === 'ok' ? 'Synced!' : 'Sync ring'}
            className={[
              'p-2.5 rounded-full border shadow-sm transition-colors',
              ringSyncState === 'syncing' ? 'bg-neutral-100 border-neutral-200/50 text-neutral-300' :
              ringSyncState === 'ok' ? 'bg-green-50 border-green-200/50 text-green-600' :
              ringSyncState === 'error' ? 'bg-red-50 border-red-200/50 text-red-500' :
              'bg-white border-neutral-200/50 text-neutral-500 hover:text-neutral-900',
            ].join(' ')}
          >
            <Watch size={18} strokeWidth={1.5} className={ringSyncState === 'syncing' ? 'animate-pulse' : ''} />
          </button>
          <button className="p-2.5 rounded-full bg-white border border-neutral-200/50 shadow-sm text-neutral-500 hover:text-neutral-900 transition-colors" aria-label="Notifications" type="button">
            <Bell size={18} strokeWidth={1.5} />
          </button>
          <button type="button" aria-label="Back" onClick={onBack} className="w-10 h-10 rounded-full bg-neutral-200 border border-neutral-300" />
        </div>
      </header>

      <main className="max-w-6xl mx-auto grid grid-cols-1 md:grid-cols-12 gap-4 pb-8">
        <section className="md:col-span-8 flex flex-col gap-6">
          <div className="grid grid-cols-3 gap-3">
            <Card>
              <div className="flex items-center gap-2 text-neutral-500 mb-3">
                <Heart size={16} strokeWidth={1.5} />
                <span className="text-xs font-medium uppercase tracking-wider">Heart</span>
              </div>
              <div className="flex items-end gap-1 mb-1">
                <span className="text-2xl sm:text-3xl font-light tracking-tighter">{currentHr ?? '--'}</span>
                {currentHr != null && <span className="text-[10px] sm:text-xs text-neutral-400 mb-0.5">bpm</span>}
              </div>
              <p className="text-[10px] text-neutral-400">{restingHr != null ? `Rest ${restingHr}` : currentHr != null ? '' : 'Sync ring'}</p>
            </Card>

            <Card>
              <div className="flex items-center gap-2 text-neutral-500 mb-3">
                <Activity size={16} strokeWidth={1.5} />
                <span className="text-xs font-medium uppercase tracking-wider">Steps</span>
              </div>
              <div className="flex items-end gap-1 mb-1">
                <span className="text-2xl sm:text-3xl font-light tracking-tighter">{activityValue ?? '--'}</span>
              </div>
              <p className="text-[10px] text-neutral-400">{summary?.ring?.steps != null ? `${((summary.ring.distance_m || 0) / 1609).toFixed(1)} mi` : ''}</p>
            </Card>

            <Card>
              <div className="flex items-center gap-2 text-neutral-500 mb-3">
                <Inbox size={16} strokeWidth={1.5} />
                <span className="text-xs font-medium uppercase tracking-wider">Queue</span>
              </div>
              <div className="flex items-end gap-1 mb-1">
                <span className="text-2xl sm:text-3xl font-light tracking-tighter">{queueLabel}</span>
              </div>
              <p className="text-[10px] text-neutral-400">{summary ? `${retryTodos} retry` : ''}</p>
            </Card>
          </div>

          <Card className="flex flex-col">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <Moon size={16} className="text-neutral-500" strokeWidth={1.5} />
                <h2 className="text-sm font-medium">Sleep</h2>
              </div>
              <span className="text-xs text-neutral-300">No data</span>
            </div>
            <div className="flex items-end gap-2 mb-1">
              <span className="text-3xl font-light tracking-tighter text-neutral-300">--</span>
              <span className="text-sm text-neutral-300 mb-1">h total</span>
            </div>
            <p className="text-xs text-neutral-400 mt-1">Wear ring overnight to track sleep</p>
          </Card>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
            <Card>
              <div className="flex items-center gap-2 text-neutral-500 mb-6">
                <MapPin size={16} strokeWidth={1.5} />
                <span className="text-xs font-medium uppercase tracking-wider">Creative Footprint</span>
              </div>

              <div className="space-y-4">
                {footprint.map((loc, i) => (
                  <div key={i}>
                    <div className="flex justify-between text-sm mb-1.5">
                      <span className="font-medium">{loc.name}</span>
                      <span className="text-neutral-400">{loc.words.toLocaleString()} w</span>
                    </div>
                    <div className="w-full h-1.5 bg-neutral-100 rounded-full overflow-hidden">
                      <div className="h-full bg-neutral-300 rounded-full" style={{ width: `${loc.percent}%` }}></div>
                    </div>
                  </div>
                ))}
              </div>
            </Card>

            <Card>
              <div className="flex items-center gap-2 text-neutral-500 mb-2">
                <Sparkles size={16} strokeWidth={1.5} />
                <span className="text-xs font-medium uppercase tracking-wider">AI Co-authoring</span>
              </div>

              <div className="flex flex-col items-center justify-center py-2 text-center h-full pb-4">
                <div className="w-full h-[140px] min-h-[140px] relative -mt-2 mb-2">
                  <ResponsiveContainer width="100%" height="100%" minHeight={140} minWidth={140}>
                    <PieChart>
                      <Pie
                        data={coAuthorData}
                        cx="50%"
                        cy="50%"
                        innerRadius={45}
                        outerRadius={65}
                        startAngle={90}
                        endAngle={-270}
                        dataKey="value"
                        stroke="none"
                        paddingAngle={2}
                      >
                        {coAuthorData.map((entry, index) => (
                          <Cell key={`cell-${index}`} fill={entry.color} />
                        ))}
                      </Pie>
                    </PieChart>
                  </ResponsiveContainer>

                  <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                    <span className="text-3xl font-light tracking-tighter">{aiPercent != null ? aiPercent : '--'}{aiPercent != null && <span className="text-base text-neutral-400">%</span>}</span>
                  </div>
                </div>

                <p className="text-xs text-neutral-400 max-w-[200px] leading-relaxed">
                  Generated or refined by AI models this week. You manually drafted <strong>{digest.user_words.toLocaleString()}</strong> units.
                </p>
              </div>
            </Card>
          </div>
        </section>

        <section className="md:col-span-4 flex flex-col gap-6">
          <Card>
            <div className="flex items-center justify-between mb-6">
              <div className="flex items-center gap-2 text-neutral-500">
                <CalendarIcon size={16} strokeWidth={1.5} />
                <span className="text-xs font-medium uppercase tracking-wider">Mood / Log</span>
              </div>
              <span className="text-xs text-neutral-400">{calendar.label}</span>
            </div>

            <div className="grid grid-cols-7 gap-y-3 gap-x-1 sm:gap-x-2 text-center text-xs">
              {['S', 'M', 'T', 'W', 'T', 'F', 'S'].map((day, i) => (
                <div key={`header-${i}`} className="text-neutral-400 font-medium mb-1">{day}</div>
              ))}

              {Array.from({ length: calendar.firstDay }).map((_, i) => (
                <div key={`pad-${i}`} className="h-8"></div>
              ))}

              {Array.from({ length: calendar.daysInMonth }).map((_, i) => {
                const dayStr = String(i + 1);
                const emoji = calendar.emojis[i + 1];
                return (
                  <div
                    key={`day-${i}`}
                    className={`h-8 flex items-center justify-center rounded-lg transition-colors ${!emoji ? 'text-neutral-300' : 'bg-neutral-100/50 grayscale-[20%]'}`}
                  >
                    {emoji ? <span className="text-base sm:text-lg saturate-50">{emoji}</span> : dayStr}
                  </div>
                );
              })}
            </div>
          </Card>

          <Card className="flex-1">
            <div className="flex items-center gap-2 text-neutral-500 mb-6">
              <FileText size={16} strokeWidth={1.5} />
              <span className="text-xs font-medium uppercase tracking-wider">Favilla Usage</span>
            </div>

            <div className="mb-6">
              <span className="text-3xl font-light tracking-tighter">{summary ? usageTotal : '--'}</span>
              <span className="text-sm text-neutral-400 ml-1">{summary ? 'turns this week' : ''}</span>
            </div>

            <div className="h-[120px] w-full flex items-end justify-between gap-1 mt-auto">
              {usage.map((item, i) => (
                <div key={i} className="flex flex-col items-center gap-2 flex-1 group">
                  <div
                    className="w-full bg-neutral-200 group-hover:bg-neutral-300 transition-colors rounded-t-sm"
                    style={{ height: `${(item.h / 5) * 100}%` }}
                  ></div>
                  <span className="text-[10px] text-neutral-400">{item.day[0]}</span>
                </div>
              ))}
            </div>
          </Card>
        </section>
      </main>
    </div>
  );
}