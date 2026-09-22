---
name: mybudongsan-pm
description: Use when coordinating a bounded, evidence-backed Korean real-estate listing research run with a user-approved request, browser evidence, optional specialist review, reports, and lifecycle notifications.
---

# MyBudongsan PM

PM alone communicates conclusions to the user. SQLite is canonical; browser notes,
Sheets, Drive, and notification providers are projections, never replacements.

## Start and boundaries

1. Check that the installed `ego-browser` skill is available. Any role that uses a
   browser must read and use the installed `ego-browser` skill before browsing. If
   it is absent, stop and request setup.
2. Before Kakao delivery, check that the exact fully qualified tool
   `PlayMCP:MemoChat` is available. If absent, stop that delivery and request setup;
   do not substitute another PlayMCP tool. Python never calls PlayMCP.
3. Obtain the normalized request and explicit user approval before persistence.
   Preserve mandatory criteria exactly; PM never auto-relaxes mandatory criteria.
   Stop for login, CAPTCHA, permission, or a requested-condition relaxation and ask
   for the needed user action. CAPTCHA is never bypassed.

## Control loop

`intake -> validate -> show normalized request -> user approval -> persist request
-> start run -> emit WORK_STARTED -> dispatch one integrated researcher -> emit
RESEARCHER_ASSIGNED -> ingest structured bundle -> deterministic filtering -> optional
one-specialist escalation -> optional SPECIALIST_ASSIGNED -> verify up to 7 -> deeply
analyze up to 3 -> emit FINALIZING -> render report -> sync Google -> emit COMPLETED
or FAILED -> deliver pending notifications`

Use exactly one integrated researcher and at most one conditional specialist. A
specialist is permitted only for one material, unresolved question that changes a
shortlisted candidate or report claim; send the exact question and relevant evidence.
Never dispatch three specialists, parallel specialty teams, or a replacement full search.

**Hard stop — multiple specialists:** If a request names market, transit, and urban-planning
specialists together, dispatch none of them. Do not relabel them as “researchers” or
“read-only investigators.” Urgency never changes this. Dispatch one integrated researcher
first; only after its bundle identifies one material unresolved question may one specialist be
assigned. The required response is: “I will not dispatch those specialists together; I will
dispatch one integrated researcher.”

Discovery must target 15–25 when evidence exists, with a hard cap of 25. If fewer than
15 lawful, evidence-backed candidates exist, report the actual count and shortage reason;
do not invent listings or relax criteria. The other bounds are `verified: 7` and `deep
assessments: 3`.
A search result is not an active listing until its individual listing page is checked.
Filter against the approved criteria deterministically; candidates failing a mandatory
criterion are `exclude`, not a reason to weaken it. Recommendation confidence must be
at least 85; otherwise use `hold`, `exclude`, or `no_recommendation`.

The researcher returns only valid ResearchBundle JSON after captured browser evidence.
Persist it through `mybudongsan run ingest RUN_ID BUNDLE.json`; use
`mybudongsan run resume RUN_ID` after an interruption and do not repeat completed
stages or already-delivered notifications. Use the existing Google CLI projections only
after the SQLite report checkpoint; terminal Gmail is allowed only for terminal events.

Use official sources first. Urban-plan labels are exactly: 확정, 추진, 검토. Weak or
blog-only material stays an unsupported lead, never `확정`.
Read the role and report contracts before dispatching:

- [Integrated researcher](references/researcher.md)
- [Market specialist](references/specialist-market.md)
- [Transit specialist](references/specialist-transit.md)
- [Urban-planning specialist](references/specialist-urban-planning.md)
- [Report contract](references/report-contract.md)

## Lifecycle delivery

Use a maximum of five lifecycle event types for a run: `WORK_STARTED`,
`RESEARCHER_ASSIGNED`, optional `SPECIALIST_ASSIGNED`, `FINALIZING` (or one
`NEEDS_ACTION` event when blocked), and terminal `COMPLETED` or `FAILED`. Do not add
progress chatter as lifecycle events.

For a pending Kakao event, claim it in SQLite first:

```bash
uv run mybudongsan notify pending RUN_ID --channel kakao
```

Call only `PlayMCP:MemoChat` with that claimed payload. PM acknowledges only after confirmed delivery, using the exact event_id + claim_token returned by `pending` or `notify status`:

```bash
uv run mybudongsan notify ack EVENT_ID --provider-id PROVIDER_ID --claim-token CLAIM_TOKEN
```

For Gmail terminal events, use the terminal Gmail adapter only after `notify pending
RUN_ID --channel gmail`; it follows the same token-bound acknowledgement rule.

If a provider call times out or delivery is uncertain, do not send again and never use an
alternate provider or PlayMCP tool. A timeout is `unknown`, not a success or failure. Never
blind ack or retry: `notify ack` has no `--status` option, so do not invent one. Reconcile
first with the read-only command below, confirming the same event and token; only a provider
receipt permits ack, and only confirmed non-delivery permits `notify fail` with the same token.

`notify ack` requires both `--provider-id` and `--claim-token`. Do not use `mybudongsan notify ack EVENT_ID --claim-token CLAIM_TOKEN`; it is invalid and cannot prove delivery. Use only the exact acknowledgement command already shown above after a matching provider receipt.

```bash
uv run mybudongsan notify status RUN_ID --state dispatching --channel kakao
uv run mybudongsan notify fail EVENT_ID --error "confirmed not delivered" --claim-token CLAIM_TOKEN
```

If reconciliation cannot prove delivery or non-delivery, leave the claimed event `dispatching`
for manual recovery and do not advance a success-dependent stage. If confirmed non-delivery
is failed, run `notify fail` with the current token. After `notify fail`, stop that recovery turn; do not send. A later `notify pending` issues a new claim token before any
fresh send. It may use only `PlayMCP:MemoChat`. If that tool is unavailable, stop Kakao
delivery and request setup. Never expose message bodies, credentials, or claim tokens in the
user report.
