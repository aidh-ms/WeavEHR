# Comments, docstrings, and attribution

A useful comment preserves information a future reader cannot reliably recover
from the code: **why a choice exists, what an assumption means, or where a borrowed
rule came from**. Length is a consequence of that need, not a target.

## What belongs where

| Information | Home |
| --- | --- |
| Non-obvious conversion, source anomaly, boundary, or invariant | Short comment next to the affected expression |
| Public callable's inputs, output, side effects, and failure contract | Docstring |
| Clinical definition, evidence window, missingness, upstream differences | Clinical method reference; local comment for the particular surprising choice |
| Shared upstream revision and mapping scope | `configs/SOURCES.md` |
| Exact upstream algorithm used by one module | One module/class source pointer, with the pinned source in the index |
| Required copyright, licence, or attribution notice | Preserve in its required location; a source index is not a replacement |
| Why this PR was made, experiments, review discussion | PR description or linked issue |
| How to install or operate the package | User documentation |

## Write the reason beside the choice

Prefer one or two sentences immediately above the relevant line. Explain the
source and target unit when a multiplier is otherwise opaque. For example:

```python
# Invalidate completion before mutating output so a crash forces recomputation.
completion_marker.unlink(missing_ok=True)
```

```yaml
# The source stores height in inches; the shared concept is in centimetres.
numeric_value: col(numeric_value) * 2.54
```

Avoid narrating the operation (`# Multiply by 2.54`), repeating item IDs already
visible in a regex, decorative section banners, commented-out code, and paragraphs
of commit history. A comment saying "same as ricu" is insufficient: state the
actual rule and point to a source when its origin matters.

Do not remove unusual thresholds, inclusion/exclusion rationales, limitations,
uncertain source interpretations, or differences from a reference merely to shorten
a file. Such details may be essential to scientific review.

## Give credit at the right level

A project-wide acknowledgment recognises architectural inspiration. A grouped
source-index entry records the upstream repository, revision, file, local scope,
and what was reused. Add a short local pointer where a mapping or algorithm would
otherwise be hard to trace. Do not repeat a full commit SHA in dozens of files.

Distinguish **inspiration**, **item selection / unit convention**, and **adapted
algorithm or copied code**. Do not imply code was copied when the evidence only
supports consulting a reference. Do not invent a revision for old provenance;
mark it unrecorded. When copying/adapting code, review and retain the applicable
notices. Existing legal notices are outside routine comment cleanup.

For a new source entry, record:

- upstream project and immutable file/revision when known;
- the local files or unambiguous group covered;
- what was used, and material differences;
- any retained notice or licence location relevant to copied material.

The [source index](../reference/sources.md) is the model. Per-line citations are
reserved for a local choice that needs its own evidence. Scientific thresholds
should cite the defining method in the method reference, not only another package.

## Docstrings describe contracts

Lead with a short action statement. Add parameters only when their meaning is not
clear from the signature. Document return shape, units, time/episode scope,
mutation and I/O, missingness, and important exceptions where relevant. Keep
algorithm background in method docs; do not copy the whole guide into each method.
Use the repository's Google-style format and type annotations.

## Review a comment-only change

Check that executable Python and parsed YAML values are unchanged. Preserve source
traceability and notices. Check that shortened prose still explains every unusual
choice. A comment cleanup must not quietly change a clinical rule or claim a new
validation result.
