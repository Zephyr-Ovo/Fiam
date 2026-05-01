# Favilla

Mobile companion app for Fiam. Functional scope: Chat, Read (co-reading), Stroll (companion view for Limen wearable).

## Stack

- Vite + React 19 + TypeScript
- Tailwind v4 + shadcn/ui (new-york, neutral base, lucide icons)
- framer-motion for transitions
- Capacitor 7 → Android (`cc.fiet.favilla`)
- Anthropic Serif/Sans/Mono bundled

## Layout

```
app/                       Vite project root
  src/
    assets/fonts/          Anthropic TTFs
    components/ui/         shadcn components (added on demand)
    lib/utils.ts           cn() helper
  android/                 Capacitor Android project
  components.json          shadcn config
refs/                      design references (page/dark/color/design/crow/font)
DEVLOG.md
README.md
```

## Develop

```pwsh
cd channels/favilla/app
npm run dev          # vite dev server, http://localhost:5173
npm run build        # production bundle into dist/
npx cap sync android # copy dist into android project
npx cap open android # open in Android Studio for run/install
```

## Status

Skeleton ready. First screen (Chat) pending vibe-anchoring step. See DEVLOG.md.
