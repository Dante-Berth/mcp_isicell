# No `metaDataDebug` markers for state behaviour or transition conditions

Verified against the `isicell-main` snapshot vendored beside this file; line
numbers are from that snapshot.

## What happens

`grep -rn metaDataDebug` over the tree returns four emission sites and one
consumer:

```
core/src/cells/PrimoCell.hpp:182      cell functions
core/src/scenarios/Scenario.hpp:39    scenario functions
core/src/scenarios/Scenario.hpp:168   codeInit
core/src/scenarios/Scenario.hpp:179   codeLoop
scripts/Isigen.py:29                  the collector
```

Nothing marks the two generated switches. `behave()` is built at
`PrimoCell.hpp:98` from each state's `code`, and `nextState()` at
`PrimoCell.hpp:115` from `buildConditionsCode(destinations)`; both substitute
the same `caseTemplate` (`PrimoCell.hpp:18`), which carries no marker. A state's
behaviour and a transition's condition therefore produce no `metaInfo` entry,
and a compile error in either yields a generated-source location and nothing
else.

## Why it matters

State behaviour and transition conditions are the two places where users write
C++ by hand as a matter of course — the model *is* that code. They are also the
only user-supplied code in the pipeline with no attribution path. The elements
that already have markers (functions, `codeInit`, `codeLoop`) are the ones
edited least often.

## Suggested fix

Give `caseTemplate` a marker slot, appended to the `case` line so the existing
collector regex picks it up unchanged:

```
case State::$name : {//isigen:metaDataDebug[$markerKey] {"state":"$name"}
```

The key has to distinguish the two switches. `Isigen.py:30` builds
`debugData[fileName]` as a dict keyed by `metaDataKey`, and both switches emit a
case for the same state names, so a bare `$name` would have `nextState`'s entry
silently overwrite `behave`'s. Something like `state:$name` and
`transition:$name` keeps them apart.

## Why a consumer working around it is not a substitute

The owning element *is* recoverable from the generated file: find the nearest
`case State::<name>` at or above the error line, and note whether it sits inside
`behave()` or `nextState()`. We do exactly that, and it works.

It is still inference from generated layout rather than a fact the generator
reported. It breaks the moment `caseTemplate` changes shape, it cannot
distinguish two destinations inside one condition block, and every consumer that
wants the mapping has to reimplement it. A marker survives template changes and
costs one substitution.
