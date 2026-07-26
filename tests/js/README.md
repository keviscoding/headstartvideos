# Plan chooser test

Covers the trial-conversion plan chooser in `webapp/static/app.js` — the dialog
that decides what a customer is charged. It slices the chooser block out of
`app.js` and runs it against a real DOM, so it exercises the shipped code rather
than a reimplementation that could drift.

`jsdom` is not a project dependency (there is no `package.json` here), so install
it anywhere and point the test at it:

```bash
mkdir -p /tmp/uitest && cd /tmp/uitest && npm install jsdom
JSDOM_PATH=/tmp/uitest/node_modules/jsdom node tests/js/test_plan_chooser.js
```

Exit code is non-zero if any check fails.

If it reports `slice missed the chooser`, the chooser moved within `app.js` —
update the `lines.slice(...)` bounds to the new range.
