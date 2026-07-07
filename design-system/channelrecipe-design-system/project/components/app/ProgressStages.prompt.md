**ProgressStages** — the cooking/waiting state. A real progress bar plus named, narrated stages (writing script → generating voiceover → assembling b-roll → rendering) with a mono ETA. Honest waiting builds trust.

```jsx
<ProgressStages
  percent={42}
  eta="about 3 minutes"
  stages={[
    { label: "Writing script", state: "done" },
    { label: "Generating voiceover", state: "active" },
    { label: "Assembling b-roll", state: "todo" },
    { label: "Rendering", state: "todo" },
  ]}
/>
```
