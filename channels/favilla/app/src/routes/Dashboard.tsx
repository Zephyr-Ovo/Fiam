import { Activity, Bell, Calendar as CalendarIcon, FileText, Moon, Watch, MapPin, Sparkles } from 'lucide-react';
import { PieChart, Pie, Cell, Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { fetchDashboardSummary, type DashboardHistoryDigest, type DashboardSummary } from '../lib/api';

const sleepData = [
  { time: '23:00', stage: 3, name: 'Awake' },
  { time: '23:30', stage: 1, name: 'Light' },
  { time: '01:00', stage: 0, name: 'Deep' },
  { time: '02:30', stage: 1, name: 'Light' },
  { time: '04:00', stage: 2, name: 'REM' },
  { time: '04:30', stage: 1, name: 'Light' },
  { time: '05:30', stage: 0, name: 'Deep' },
  { time: '06:30', stage: 1, name: 'Light' },
  { time: '07:00', stage: 2, name: 'REM' },
  { time: '07:30', stage: 3, name: 'Awake' },
];

const favillaUsage = [
  { day: 'Mon', h: 1.2 }, { day: 'Tue', h: 3.5 }, { day: 'Wed', h: 2.1 },
  { day: 'Thu', h: 4.8 }, { day: 'Fri', h: 3.0 }, { day: 'Sat', h: 1.5 },
  { day: 'Sun', h: 0.8 }
];

const locationData = [
  { name: 'Studio', words: 12400, percent: 65 },
  { name: 'Cafe (Roast)', words: 4200, percent: 22 },
  { name: 'Transit', words: 2450, percent: 13 }
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
  if (!summary) return locationData;
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

  useEffect(() => {
    let cancelled = false;
    fetchDashboardSummary().then((result) => {
      if (!cancelled && result.ok) setSummary(result);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const digest = useMemo(() => totalDigest(summary), [summary]);
  const usage = useMemo(() => usageFromSummary(summary), [summary]);
  const usageTotal = useMemo(() => usageTurns(summary), [summary]);
  const footprint = useMemo(() => footprintFromSummary(summary), [summary]);
  const calendar = useMemo(() => calendarFromSummary(summary), [summary]);
  const aiPercent = digest.words ? Math.round((digest.ai_words / digest.words) * 100) : 32;
  const coAuthorData = [
    { name: 'Human', value: Math.max(0, 100 - aiPercent), color: '#262626' },
    { name: 'AI Refined', value: aiPercent, color: '#d4d4d4' }
  ];
  const flowBeats = summary?.status?.flow_beats || 0;
  const activityValue = summary ? (flowBeats / 1000).toFixed(1) : '8.4';
  const pendingTodos = summary?.todos?.length || 0;
  const queueLabel = summary ? (pendingTodos ? String(pendingTodos) : 'Clear') : 'Low';
  const retryTodos = summary?.health?.retry_todos || 0;

  return (
    <div className="min-h-screen bg-neutral-100 text-neutral-900 p-8 font-sans selection:bg-neutral-200 overflow-y-auto">
      <header className="max-w-6xl mx-auto flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-medium tracking-tight">Morning, Iris</h1>
          <p className="text-sm text-neutral-500 mt-1">{formatStatusLine(summary)}</p>
        </div>
        <div className="flex items-center gap-3">
          <button className="p-2.5 rounded-full bg-white border border-neutral-200/50 shadow-sm text-neutral-500 hover:text-neutral-900 transition-colors" aria-label="Notifications" type="button">
            <Bell size={18} strokeWidth={1.5} />
          </button>
          <button type="button" aria-label="Back" onClick={onBack} className="w-10 h-10 rounded-full bg-neutral-200 border border-neutral-300" />
        </div>
      </header>

      <main className="max-w-6xl mx-auto grid grid-cols-1 md:grid-cols-12 gap-6 pb-[env(safe-area-inset-bottom)]">
        <section className="md:col-span-8 flex flex-col gap-6">
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 sm:gap-6">
            <Card>
              <div className="flex items-center gap-2 text-neutral-500 mb-3">
                <Watch size={16} strokeWidth={1.5} />
                <span className="text-xs font-medium uppercase tracking-wider">Heart</span>
              </div>
              <div className="flex items-end gap-2 mb-1">
                <span className="text-3xl sm:text-4xl font-light tracking-tighter">72</span>
                <span className="text-xs sm:text-sm text-neutral-400 mb-1">bpm</span>
              </div>
              <p className="text-[10px] sm:text-xs text-neutral-400">Avg 65 bpm</p>
            </Card>

            <Card>
              <div className="flex items-center gap-2 text-neutral-500 mb-3">
                <Activity size={16} strokeWidth={1.5} />
                <span className="text-xs font-medium uppercase tracking-wider">Activity</span>
              </div>
              <div className="flex items-end gap-1 sm:gap-2 mb-1">
                <span className="text-3xl sm:text-4xl font-light tracking-tighter">{activityValue}</span>
                <span className="text-[10px] sm:text-sm text-neutral-400 mb-1">k</span>
              </div>
              <p className="text-[10px] sm:text-xs text-neutral-400">{summary ? 'Flow beats' : 'Steps • 3.2 mi'}</p>
            </Card>

            <Card className="col-span-2 sm:col-span-1">
              <div className="flex items-center gap-2 text-neutral-500 mb-3">
                <Activity size={16} strokeWidth={1.5} />
                <span className="text-xs font-medium uppercase tracking-wider">Stress</span>
              </div>
              <div className="flex items-end gap-2 mb-1">
                <span className="text-3xl sm:text-4xl font-light tracking-tighter">{queueLabel}</span>
              </div>
              <p className="text-[10px] sm:text-xs text-neutral-400">{summary ? `${retryTodos} retry • todo queue` : 'Avg 24'}</p>
            </Card>
          </div>

          <Card className="min-h-[300px] flex flex-col">
            <div className="flex items-center justify-between mb-6">
              <div className="flex items-center gap-2">
                <Moon size={16} className="text-neutral-500" strokeWidth={1.5} />
                <h2 className="text-sm font-medium">Sleep Architecture</h2>
              </div>
              <div className="flex gap-4 text-xs text-neutral-400">
                <div className="flex items-center gap-1.5"><div className="w-2 h-2 rounded-full bg-neutral-200"></div>Deep</div>
                <div className="flex items-center gap-1.5"><div className="w-2 h-2 rounded-full bg-neutral-300"></div>Light</div>
                <div className="flex items-center gap-1.5"><div className="w-2 h-2 rounded-full bg-neutral-400"></div>REM</div>
              </div>
            </div>
            <div className="flex-1 w-full -ml-4 mt-2">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={sleepData}>
                  <defs>
                    <linearGradient id="sleepColor" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#a3a3a3" stopOpacity={0.4}/>
                      <stop offset="95%" stopColor="#a3a3a3" stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="time" axisLine={false} tickLine={false} tick={{fontSize: 11, fill: '#a3a3a3'}} dy={10} />
                  <YAxis hide domain={[-0.5, 3.5]} />
                  <Tooltip
                    contentStyle={{ borderRadius: '8px', border: '1px solid #e5e5e5', boxShadow: 'none' }}
                    itemStyle={{ color: '#171717' }}
                    formatter={(val) => {
                      const stage = typeof val === 'number' ? val : Number(val);
                      const stages = ['Deep', 'Light', 'REM', 'Awake'];
                      return [stages[stage] || 'Unknown', 'Stage'];
                    }}
                  />
                  <Area type="stepAfter" dataKey="stage" stroke="#737373" strokeWidth={2} fillOpacity={1} fill="url(#sleepColor)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
            <div className="mt-4 pt-4 border-t border-neutral-100 flex items-center justify-between">
              <div className="flex items-end gap-2">
                <span className="text-3xl font-light tracking-tighter">7</span>
                <span className="text-sm text-neutral-400 mb-1">h</span>
                <span className="text-3xl font-light tracking-tighter ml-1">12</span>
                <span className="text-sm text-neutral-400 mb-1">m</span>
              </div>
              <p className="text-xs text-neutral-400">Score 88 • Last night</p>
            </div>
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
                <div className="w-full h-[140px] relative -mt-2 mb-2">
                  <ResponsiveContainer width="100%" height="100%">
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
                    <span className="text-3xl font-light tracking-tighter">{aiPercent}<span className="text-base text-neutral-400">%</span></span>
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
              <span className="text-3xl font-light tracking-tighter">{summary ? usageTotal : '16.9'}</span>
              <span className="text-sm text-neutral-400 ml-1">{summary ? 'turns this week' : 'hrs this week'}</span>
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