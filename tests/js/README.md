# Frontend tests

`test_pricing_modal.js` loads the real `index.html` and `app.js` into a DOM and
exercises the trial paywall, which is the path every paying customer walks
through. It covers what the CTA promises to charge, that both tiers and both
billing intervals are reachable, that a trial converts in place instead of
hitting Stripe Checkout (which the server refuses for anyone already
subscribed), and that the action a customer was blocked on resumes after the
charge lands.

`jsdom` is a test-only dependency and is deliberately not in the project's
requirements, so install it on demand:

```bash
npm i --no-save jsdom
node tests/js/test_pricing_modal.js
```

Exit code is non-zero if any check fails.
