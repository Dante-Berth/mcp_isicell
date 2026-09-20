# Upstream defect reports

Findings in ISiCell itself, written to be filed against the ISiCell project
rather than fixed here. Each was verified by reading the `isicell-main` source
snapshot and cites the lines it was read from. That snapshot is not in this
repository -- it is third-party and `.gitignore`d -- so the line numbers are
worth re-checking against a current checkout before filing.

Nothing here has been filed. They are drafts, ready to paste.

- [Single-line code loses its `metaDataDebug` marker](single-line-code-loses-its-marker.md)
  — four emission sites consume a newline that single-line code does not have,
  so the code most likely to be written inline is the code that loses its
  attribution. Warned about locally by `W_FUNC_SINGLE_LINE`.
- [No `metaDataDebug` markers for state behaviour or transition conditions](no-markers-for-state-code-or-transitions.md)
  — the two switches built from `caseTemplate` carry no markers, so the
  likeliest place for hand-written C++ to be wrong is the one place with no way
  back to the model. Worked around locally by `locate_by_case_label`, which
  infers the owner from the generated `case State::<name>` labels.

The other findings recorded in `ROADMAP.md` (the no-op `validatePackageName`
wiring, the concatenated SQL in `getStep`, `get_conditions()` raising
`NameError`, and the plaintext password in `backend/dbInfo.json`) were found by
reading rather than by running, and are not written up here.
