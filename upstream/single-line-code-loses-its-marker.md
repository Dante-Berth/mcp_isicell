# Single-line code loses its `metaDataDebug` marker

Verified by reading the `isicell-main` source snapshot; line numbers are from
that snapshot and may drift. The snapshot itself is not in this repository --
it is a third-party blob, kept out by `.gitignore` -- so check the lines against
your own checkout before filing.

## What happens

Four templates attach a position marker to user code by consuming its first
newline:

| Site | Covers |
|---|---|
| `core/src/cells/PrimoCell.hpp:182` | cell functions |
| `core/src/scenarios/Scenario.hpp:39` | scenario functions |
| `core/src/scenarios/Scenario.hpp:168` | `codeInit` |
| `core/src/scenarios/Scenario.hpp:179` | `codeLoop` |

Each is a variation on

```python
code.replace('\n', '//isigen:metaDataDebug[' + name + '] ' + meta + '\n', 1)
```

which puts the marker at the end of the first line. When `code` holds no
newline there is nothing to replace: `str.replace` returns the string unchanged
and **no marker is emitted at all**. The collector at `scripts/Isigen.py:29-31`
therefore records no entry, and `debugData` carries no `metaInfo` for that
element.

The consequence is that a compiler diagnostic inside such code can only be
reported against generated C++. Nothing connects it back to the function or the
scenario block the user actually wrote.

## Why it matters

The shorter the snippet, the likelier someone writes it on one line, so the
rule is inverted: the code most likely to be written inline is the code that
loses its attribution.

```cpp
double area() { return 3.14 * r * r; }
```

gets no marker. The same body split across two lines gets one.

`codeInit` and `codeLoop` are the same story and are easy to miss, because a
one-line initialiser is entirely idiomatic.

## Reproducing

1. Define a cell function whose `code` is a single line.
2. Generate.
3. `debugData` has no entry for that `idFunc`. Split the body across two lines
   and the entry appears.

## Suggested fix

Attach the marker to the first line without requiring a newline to consume:

```python
head, sep, tail = code.partition('\n')
print(head + marker + '\n' + tail)
```

One note on the obvious alternative: prepending the marker on its own line also
always emits it, but it moves every reported line by one. `Isigen.py:30` records
the line the *marker* sits on, counting newlines before the match, and
`Isigen.py:32` strips the marker leaving that line in place — so a prepended
marker reports the blank line above the signature. `partition` keeps the
reported line exactly where it is today for multi-line code, which means the fix
changes nothing for code that currently works.

## Adjacent, same line

The same expressions call

```python
re.search(r'(\w+[\d_\w]+)\s*\(', code).group(1)
```

unguarded. Code from which no name parses raises `AttributeError` from inside
the template, naming neither the function nor the block. A guarded branch with a
message would turn that into something a user can act on. Separate issue, same
line.
