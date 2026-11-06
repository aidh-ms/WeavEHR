"""Check built HTML links and fragments without making network requests."""

import argparse
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


class Links(HTMLParser):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.targets: set[str] = set()
        self.links: list[str] = []
        self.feed(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        for key in ("id", "name"):
            if value := values.get(key):
                self.targets.add(value)
        if tag == "a" and (href := values.get("href")):
            self.links.append(href)


def check(root: Path) -> list[str]:
    root = root.resolve()
    pages = {path: Links(path.read_text()) for path in root.rglob("*.html")}
    errors = []
    for path, page in pages.items():
        for href in page.links:
            url = urlsplit(href)
            if url.scheme or url.netloc:
                continue
            target = (path.parent / unquote(url.path)).resolve() if url.path else path
            if url.path.startswith("/"):
                target = root / unquote(url.path).lstrip("/")
            if target.is_dir():
                target /= "index.html"
            if not target.exists():
                errors.append(f"{path.relative_to(root)}: missing {href}")
            elif url.fragment and target in pages and unquote(url.fragment) not in pages[target].targets:
                errors.append(f"{path.relative_to(root)}: missing fragment {href}")
    return sorted(set(errors))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site", type=Path, nargs="?", default=Path("site"))
    args = parser.parse_args()
    if not (args.site / "index.html").is_file():
        parser.error("Build the site first with mkdocs build --strict")
    errors = check(args.site)
    if errors:
        raise SystemExit("\n".join(errors))
    print("All built HTML links and fragments resolve.")
