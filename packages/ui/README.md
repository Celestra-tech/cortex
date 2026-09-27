# @celestra/cortex-ui

CELESTRA Cortex design system: shadcn/ui primitives on Tailwind CSS v4 with a
monochrome token set (light and dark via `prefers-color-scheme`).

```css
/* app globals.css */
@import "@celestra/cortex-ui/globals.css";
```

```tsx
import { Card, CardHeader, CardTitle } from "@celestra/cortex-ui/components/card";
```

Add more shadcn components from an app directory, e.g.
`pnpm dlx shadcn@latest add dialog` inside `apps/dashboard`.
