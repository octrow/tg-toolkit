# Contributing

Bug reports and pull requests are welcome.

## Set up

```bash
git clone https://github.com/octrow/tg-toolkit.git
cd tg-toolkit
uv sync --all-extras
uv run pytest
```

## Rules for a change

- A behavior change comes with a test.
- Don't add a dependency for something a few lines can do.
- Commit messages: `<type>: <description>` (`feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `ci`).
