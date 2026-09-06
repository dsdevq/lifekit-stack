# Tasks — 001 notify envelope

## US1 — the relay renders the envelope (`POST /notify`)

- [x] T001 `compose/notify-relay/render.js`: level table, HTML escaping, and
      rendered-length clipping helper.
- [x] T002 `render.js`: `validateEnvelope` — required fields, level vocabulary,
      link shape and `http(s)` scheme.
- [x] T003 `render.js`: `renderEnvelope` — line 1, body, collapsed `detail`,
      links, `action` last and only for `act`/`wait`; budget so the message
      always fits.
- [x] T004 `compose/notify-relay/render.test.js`: cover each rendering rule,
      the escaping, the `good`-drops-`action` fix, and the truncation path.
- [x] T005 `compose/notify-relay/server.js`: `POST /notify` — parse, validate,
      render, send with `parse_mode: HTML`; `400` on a bad envelope, `502` on a
      Telegram failure.
- [x] T006 `compose/notify-relay/Dockerfile`: copy `render.js` into the image.
- [x] T007 `devclaw.json`: `verifyCmd` runs the memory-audit pytest suite *and*
      `node --test compose/notify-relay/`.
- [x] T008 `docs/notify-envelope.md` + `README.md` row: the producer-facing
      contract, with the Grafana exemption written down.
- [x] T009 `compose/notify-relay/server.test.js`: drive the real HTTP surface —
      `/notify` sends rendered HTML with `parse_mode: HTML`, `400` without
      calling Telegram on a bad envelope, `413` on an over-large body, `502`
      when Telegram refuses, a body split mid-character still arrives whole, and
      the legacy `/text` + `/devclaw` paths still send plain text.
- [ ] T010 Run the Node suite in CI's `tests` job alongside the pytest one.
      *Deferred: `.github/workflows/ci.yml` is a gate input, not this slice's to
      edit. Until then `devclaw.json`'s `verifyCmd` is what runs both layers.*

## US2 — `/devclaw` renders through the envelope

- [ ] T101 Map the devclaw task row onto a v1 envelope in `server.js`; delete
      the hand-rolled `formatMessage`.
- [ ] T102 Tests for the task-row → envelope mapping, including
      `result_json`-as-string and unknown statuses.

## US3 — devclaw's producers post envelopes *(cross-repo: devclaw)*

- [ ] T201 Goal layer posts an envelope to `/notify`.
- [ ] T202 Task callbacks post an envelope to `/notify`.
- [ ] T203 Retire `/text` here once devclaw's side has deployed.

## US4 — Grafana's dead-man template speaks the same grammar

- [ ] T301 Re-implement line 1 and the level glyphs in the Go template.
- [ ] T302 Resolved messages drop the firing body's remediation text.

## US5 — OpenClaw agents produce envelopes

- [ ] T401 Point the agent workspace contract at `/notify` with a level table.
