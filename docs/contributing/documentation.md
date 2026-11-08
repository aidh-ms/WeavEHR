# Writing documentation

Organise documentation around the tasks below. Introduce prerequisites, a runnable
example, and its expected output before explaining the package internals.

## Readers and tasks

| Reader | Immediate question | Route and success condition |
| --- | --- | --- |
| Researcher evaluating the package | Can I get a useful result without access paperwork? | Synthetic tutorial → three expected heart-rate rows |
| Analyst with a downloaded database | What path/version do I use, and what files come out? | Run a dataset → inspect one concept and its coverage |
| Foundation-model researcher | Can I use native codes, selected clinical concepts, or both? | [Use extraction output](../guides/extract-only.md) → native events, their vocabulary and a mix with selected concepts |
| Multicentre researcher | Does this variable mean the same thing in each source? | Catalogue → effective mappings → identity/time and clinical definitions |
| Clinical methods researcher | Why does this score differ, and does it use future evidence? | Clinical methods → parameters, missingness, timing, source references |
| Data engineer adding a hospital | Which part do I extend? | Add a table/concept → small-frame validation → inheritance |
| Contributor maintaining the engine | Where does this behavior live, and what must I update? | Architecture → focused API → relevant tests and canonical docs page |

A novice should not need registries or inheritance to complete the first tutorial.
An experienced user should not need to repeat that tutorial to find a field or an
error explanation.

## Write for the reader

Describe current behaviour with concrete examples. Keep authoring reports,
responses to a requester, and commentary about how the documentation was produced
out of repository prose. Include assumptions and limitations where they affect
how someone runs, interprets, or extends the package.

## Content boundaries

| Location | Owns | Does not own |
| --- | --- | --- |
| README | Purpose, one command path, entry links | Full configuration manual |
| Start here | One successful lesson and the core mental model | Every installation or architecture option |
| How-to guides | One reader goal, prerequisites, steps, verification | Exhaustive API fields |
| Reference | Exact fields, generated inventory, method semantics | Promotional claims |
| Contribute | Architecture, workflow, editorial and comment policies | A second user manual |
| Dataset README | Source-specific representation and limitations | Repeated framework explanations or hand-counted coverage |

Give each fact one canonical home and link to it elsewhere. The site is
published, so keep page URLs stable when reorganising, or leave a redirect. Generate dataset and concept inventories
from configs. Limit the Python API navigation to public entry points that users
need to call.

## Keep examples executable

1. Update the canonical page when behavior changes. Do not append a new mini-guide
   to the README or a vaguely related section.
2. Prefer including a checked-in example file with `pymdownx.snippets` over copying it.
3. Update `tests/test_documentation.py` when changing the quickstart, transformer
   example, or complete step-config examples. The tutorial must run without credentials.
4. Run `uv run mkdocs build --strict`. Missing pages, anchors, snippets, or API
   targets must fail the check; do not silence them to make the build green.
5. Inspect the landing page, a guide, and a dense reference page at desktop and
   narrow widths when changing the theme or navigation.

Preview locally with `uv run mkdocs serve`. Every CI run does the strict build
and link check and uploads the result as a `documentation-site` artifact, so a
reviewer can download it, run `python -m http.server 8000` inside it and open
`http://localhost:8000` without checking the branch out. Merges to `main`
publish the site to <https://aidh-ms.github.io/WeavEHR/> through the
`Documentation` workflow.

Catalogue generation uses effective configs, including inheritance, and excludes
empty simple mappings from availability counts. Describe coverage in terms of
configured mappings. Document clinical validation separately with its data,
methods, and results. Pin sources and record deviations in the source index and
method notes.

## Acceptance tasks for the next new user

Ask a researcher unfamiliar with the repository to complete these tasks without
maintainer help. Record where they stop; fix the page at that point.

- Produce the synthetic result and explain the fourth source row's absence.
- Identify the raw-data root and exact version for their dataset.
- Find whether a requested concept has a non-empty mapping in two sources.
- Change a mapping, inspect a tiny frame, and rerun the correct step.
- Explain the subject key, a score's lookback, and whether its evidence is retrospective.

Aim for a first visible result within ten minutes after installation.
