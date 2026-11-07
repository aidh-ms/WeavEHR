# Contribute to WeavEHR

Dataset mappings, synthetic regression examples, and clear documentation are all
useful contributions. Start with the smallest change that makes a reader or
researcher's task work.

## Development environment

```bash
git clone https://github.com/aidh-ms/WeavEHR.git
cd WeavEHR
uv sync --frozen --all-groups
```

The included dev container is an alternative when using VS Code and Docker.
Python 3.13+ and the repository's Ruff settings define the code style.

## Make and check a change

```bash
uv run ruff format
uv run ruff check .
uv run ty check .
uv run pytest
uv run mkdocs build --strict
```

During iteration, run the relevant test module with `--no-cov`; the full suite
checks the coverage floor. New mappings should include a small synthetic example
that demonstrates item selection, units, and relevant boundary cases. Never commit
patient-level records or credentialed dataset extracts.

Preview the site with `uv run mkdocs serve`. The same strict build runs in CI.
The full build is in `site/` and can be served with a local HTTP server.

## Find the right extension point

- [Add a table or concept](../guides/extend.md) for source and mapping changes.
- [Write a transformer](../guides/transformer.md) for time-dependent calculations.
- [Architecture](../contributing/architecture.md) for changes to the engine.
- [Writing documentation](../contributing/documentation.md) and
  [comment policy](../contributing/comments.md) for prose and attribution.

Use Conventional Commit titles such as `fix(eicu): ...` or `docs: ...`.
A PR should explain the concrete problem, resulting behavior, relevant validation,
and remaining limitations. For a mapping, identify its source and intentional
differences from upstream. For a clinical rule, update its canonical method page.
