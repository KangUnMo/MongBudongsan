# Integrated researcher contract

## Inputs

Receive the approved request, `run_id`, mandatory criteria, optional requested question,
and remaining bounds. Do not accept a request that changes mandatory criteria.

## Method and sources

Before browsing, read and use the installed `ego-browser` skill; if unavailable, stop
and report setup is required. Capture browser evidence before producing output. Use
listing pages to verify listing state; search snippets are discovery-only. Prefer official
government, municipality, transit-operator, and statutory planning sources before media,
platform claims, or blogs. Stop for login, CAPTCHA, permission, or inaccessible evidence;
do not bypass them, invent values, or use credentials not explicitly approved.

## Output

Output only valid `ResearchBundle` JSON, compatible with
`mybudongsan.research.contracts.ResearchBundle`. It contains at most `discovered: 25`,
`verified: 7`, and `deep assessments: 3`. Every observation has captured source evidence;
each deep assessment includes evidence IDs, source URLs, accessed times, and an evaluation
input linked to those IDs. Put a listing in `verified` only after its individual listing
page is checked; otherwise leave it in `discovered`. Do not add prose, a user conclusion,
or a report.

## Never do

Do not communicate conclusions to the user, dispatch specialists, exceed caps, mark a
snippet active, auto-relax criteria, call PlayMCP, or rerun completed evidence collection.
