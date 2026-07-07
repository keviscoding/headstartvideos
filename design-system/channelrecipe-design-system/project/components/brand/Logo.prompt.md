**Logo** — the ChannelRecipe mark. Glyph is a recipe card with a folded-corner play triangle; the wordmark sets "Channel" in ink and "Recipe" in violet. Use `horizontal` in nav, `glyph` for favicon/app-icon contexts, `wordmark` where the glyph already appears.

```jsx
<Logo variant="horizontal" size={30} />
<Logo variant="glyph" theme="dark" size={40} />
```

Pass `theme="dark"` on the app's navy surface so the fold notch matches the background.
