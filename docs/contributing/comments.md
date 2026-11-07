# Comments, docstrings, and credit

A comment earns its place when it tells a reader something they cannot get
back from the code: why a choice was made, what an assumption means, or where
a borrowed rule came from. Everything else is noise, and noise in a config
file is worse than noise in Python because the file is supposed to be read
by clinicians as well as engineers.

## What gets a comment

Put a comment next to the line it explains, not at the top of the file, and
keep it to a sentence or two.

| Worth a comment | Example |
| --- | --- |
| A multiplier whose units are not obvious | `# µmol/L → mg/dL` above `col(numeric_value) * 0.058467` |
| An item that was deliberately left out, or an odd one that was kept | `# Item 6812, which ricu uses, is left out: its values do not fit µmol/L.` |
| A source quirk the mapping works around | `# A zero offset means "not recorded" and is dropped.` |
| An opaque identifier | `# 3027018 = Heart rate` next to the regex that matches it |
| A threshold or window that is a choice rather than a definition | `# 48h: rounds above the longest linked episode in the demo (40.6h).` |
| A deviation from the reference implementation | `# ricu also applies a 30-minute minimum duration; we do not.` |

What does not get a comment: section banners (`# db fields - values`), a
restatement of the next line (`# multiply by 2.54`), item IDs that already
appear in the regex, commented-out code, and change history. If the only
thing a comment says is "same as ricu", either state the rule or delete the
comment.

Do not remove a comment just to make a file shorter if it records a
threshold, an exclusion, a limitation, or an uncertain interpretation. Those
are the comments a reviewer of a clinical mapping actually needs.

## Where credit lives

Credit is recorded once, at the level where it is true, and not repeated in
every file it applies to.

| Level | Home |
| --- | --- |
| The project is inspired by ricu and builds on mimic-code and others | README and the landing page |
| A group of mappings follows a pinned upstream file | [`configs/SOURCES.md`](../reference/sources.md): upstream project, revision, file, the local files covered, and what differs |
| A Python module adapts a specific upstream algorithm | One line in the module docstring naming the upstream file, with the pinned revision in the index |
| A mapping file | No provenance comment at all; the index is the only place it is recorded |
| A required licence or attribution notice | Wherever its licence says it must stay; the index does not replace it |

Be precise about what was taken. Consulting a reference for which items to
select is not the same as adapting its algorithm, and neither is copying its
code. Do not invent a revision for old provenance; write "upstream revision:
unrecorded" and let someone pin it later. When code is copied or adapted,
check the licence and keep whatever notice it requires.

A scientific threshold should cite the method that defines it (the KDIGO
guideline, the Sepsis-3 paper) on the clinical reference page, not just the
package we first saw it in.

## Docstrings

A docstring describes a contract: what the callable takes, what it returns,
what it mutates or writes, and how it fails. Open with one line that says what
it does. Only document a parameter when its meaning is not obvious from the
name and type. Mention units, time scope, and missingness rules when they
affect the result. Keep the algorithm narrative on the clinical reference
page and link to it; do not paste the method into every class that uses it.
The repository uses Google-style docstrings with type annotations.

## Reviewing a comment-only change

Parse the YAML before and after and check the values are identical; a few
lines of `yaml.safe_load` over `git show` output is enough. Check that no
threshold, exclusion or limitation was dropped with the prose around it, and
that every pinned revision that was removed from a file is in the index.
