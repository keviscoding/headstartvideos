**RecipeCard** — the brand's atomic object. A rounded card whose folded top-right corner forms a play triangle (recipe + video in one shape). Use it anywhere a niche, result, or payoff is presented; the same frame backs pricing, Upload Kit, and testimonial cards.

```jsx
<RecipeCard
  name="Reddit story timers"
  promise="Faceless story videos with on-screen text and a countdown."
  cookTime="~15 min"
  credits="1 credit"
  rpm="RPM $4–8"
  status="new"
  onClick={() => pick("reddit")}
/>
```

Variants: `status="new"` (violet weekly-drop pill) · `status="proven"` (green pill + dashboard spark) · `theme="dark"` for the app surface · `selected` for the picked state.
