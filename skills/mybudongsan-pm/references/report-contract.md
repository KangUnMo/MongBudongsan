# Report contract

PM renders the report and alone communicates its conclusion to the user. It reads SQLite
records and the approved request; SQLite is canonical. A report never turns snippets into
active listings or unsupported claims into facts.

For every material claim include source URL, accessed time, evidence type, confidence, and
unsupported claims or limitations. Separate verified active listings, unverified candidates,
and excluded/inactive records. Show mandatory-criteria failures and any user-approved
relaxed comparison separately from original criteria. Urban claims use only `확정`, `추진`,
or `검토` and official sources first.

Each candidate has exactly one final status: recommend, hold, exclude, or no_recommendation.
`recommend` requires confidence >=85 and all mandatory criteria;
otherwise select the truthful non-recommendation status. On login, CAPTCHA, permission, or
missing relaxation approval, report the blocker and required user action without bypassing
it. Do not include secrets, private message bodies, claim tokens, or notification payloads.
