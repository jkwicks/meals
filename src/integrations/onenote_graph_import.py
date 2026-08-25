"""Bootstrap the recipe catalog straight from a OneNote section, no per-page
copy-paste — via the Microsoft Graph OneNote API.

    ./venv/bin/python src/integrations/onenote_graph_import.py --section "Fast 800 Program" --dry-run
    ./venv/bin/python src/integrations/onenote_graph_import.py --section "Fast 800 Program"

**One-time setup, done by a human in a browser, not by this script:**

1. portal.azure.com -> App registrations -> New registration. Supported
   account types: "Personal Microsoft accounts only". Under Authentication,
   enable "Allow public client flows".
2. API permissions -> Add a permission -> Microsoft Graph -> Delegated ->
   Notes.Read.
3. Put the registration's Application (client) ID in `.env` as
   `ONENOTE_CLIENT_ID`.

The first run then prints a `https://microsoft.com/devicelogin` URL and a
code; open it, sign in, approve once. `msal`'s serializable cache
(`data/onenote_token_cache.bin`, inside the already-gitignored `data/`) is
what makes every run after that silent — same "log in once, renew quietly"
shape `sync_service.GarminSyncService` gets for free from garminconnect's own
cached tokens, reimplemented here because Graph's device-code flow is the
generic version of that, not something garminconnect's client can do.

**Why Graph and not the two options `keep_import.py` weighed and rejected
for Keep.** `keep.googleapis.com` doesn't exist for OneNote at all in that
shape — Graph's `/me/onenote/*` *is* the live, documented, first-party API
for a personal notebook, unlike Keep's Workspace-only endpoint. So this is
the API `keep_import.py` wished it could use, not the unofficial-client
fallback it settled for. The cost that pushed Keep's import onto a Takeout
export instead — a repeatable job wasn't worth paying for — doesn't apply
here on the same terms: a OneNote recipe section keeps gaining pages long
after the first import, so "run it again in six months" is a real use this
script is built for, not a hypothetical.

**Reuses `onenote_import.import_pages` rather than duplicating it.** Fetching
pages from Graph and fetching them from a folder of hand-copied `.txt` files
produce the exact same shape — `{"title": ..., "body": ...}` — so this module
is only the fetching half; `fetch_section_pages` hands its output straight to
`onenote_import.import_pages`, which is where the parse-per-page loop, the
already-in-catalog skip, and the "one bad page must not fail the run" policy
already live. The `.txt`-folder path stays available on its own for anyone
who'd rather not do the Azure registration.
"""

import argparse
import asyncio
import os
import sys
from typing import Dict, List, Optional

import msal
import requests
from bs4 import BeautifulSoup

# See sync_service.py: src/integrations/ is one level below the flat module
# layout the rest of the app relies on, so src/ has to go on the path by hand
# before any sibling import. Insert rather than append — a stray repository.py
# elsewhere on the path must not win over the project's.
_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from onenote_import import import_pages  # noqa: E402
from planner import api_key_error, configure_logging  # noqa: E402
from repository import DATA_DIR, LocalJSONRepository  # noqa: E402

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - python-dotenv is in requirements.txt
    load_dotenv = None


GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
SCOPES = ["Notes.Read"]
# consumers, not common: the app registration this script needs is
# "Personal Microsoft accounts only", and pointing at the wrong authority is
# an opaque AADSTS error rather than a helpful one.
AUTHORITY = "https://login.microsoftonline.com/consumers"
TOKEN_CACHE_PATH = os.path.join(DATA_DIR, "onenote_token_cache.bin")


def _client_id() -> str:
    client_id = os.environ.get("ONENOTE_CLIENT_ID")
    if not client_id:
        raise RuntimeError(
            "ONENOTE_CLIENT_ID is not set. Register a free app at "
            "portal.azure.com (\"Personal Microsoft accounts only\", public "
            "client flows enabled, Notes.Read delegated permission) and put "
            "its Application (client) ID in .env as ONENOTE_CLIENT_ID — see "
            "this file's module docstring for the exact steps."
        )
    return client_id


def _load_cache() -> "msal.SerializableTokenCache":
    cache = msal.SerializableTokenCache()
    if os.path.exists(TOKEN_CACHE_PATH):
        with open(TOKEN_CACHE_PATH, "r", encoding="utf-8") as handle:
            cache.deserialize(handle.read())
    return cache


def _save_cache(cache: "msal.SerializableTokenCache") -> None:
    # has_state_changed is False on a pure silent-renewal path — writing
    # unconditionally would touch the file (and its mtime) on every run for
    # no reason.
    if not cache.has_state_changed:
        return
    os.makedirs(os.path.dirname(TOKEN_CACHE_PATH), exist_ok=True)
    with open(TOKEN_CACHE_PATH, "w", encoding="utf-8") as handle:
        handle.write(cache.serialize())


def acquire_token() -> str:
    """A Graph access token. Silent after the first run.

    The device-code flow is the one MSAL grant that needs no redirect URI and
    no client secret — right for a script with no web server of its own to
    receive a callback on, and for a "public client" app registration, which
    is the only kind Azure lets a personal Microsoft account create without
    an organisation behind it.
    """
    cache = _load_cache()
    app = msal.PublicClientApplication(
        _client_id(), authority=AUTHORITY, token_cache=cache
    )

    result = None
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])

    if not result:
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(f"Could not start the device login: {flow}")
        print(flow["message"], flush=True)
        result = app.acquire_token_by_device_flow(flow)

    _save_cache(cache)

    if "access_token" not in result:
        raise RuntimeError(
            "Graph login failed: "
            f"{result.get('error_description') or result.get('error') or result}"
        )
    return result["access_token"]


def _get(url: str, token: str, params: Optional[dict] = None) -> dict:
    response = requests.get(
        url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=30
    )
    response.raise_for_status()
    return response.json()


def find_section(token: str, name: str) -> Dict[str, str]:
    """The `{id, displayName}` of the OneNote section named `name`.

    `$filter=displayName eq '...'` is the entire search surface Graph offers
    here. A duplicate section name across two notebooks is rare enough — and
    trivially renamed around in OneNote itself — that this raises rather than
    guessing which one was meant.
    """
    escaped = name.replace("'", "''")  # OData string-literal escaping
    payload = _get(
        f"{GRAPH_ROOT}/me/onenote/sections",
        token,
        params={"$filter": f"displayName eq '{escaped}'", "$select": "id,displayName"},
    )
    sections = payload.get("value") or []
    if not sections:
        raise RuntimeError(f"No OneNote section named {name!r} was found.")
    if len(sections) > 1:
        raise RuntimeError(
            f"{len(sections)} sections are named {name!r} — rename one in "
            "OneNote so this can tell them apart."
        )
    return sections[0]


def list_pages(token: str, section_id: str) -> List[Dict[str, str]]:
    """Every page in the section — `{id, title}` — following
    `@odata.nextLink` past Graph's default page size."""
    pages: List[Dict[str, str]] = []
    url = f"{GRAPH_ROOT}/me/onenote/sections/{section_id}/pages"
    params: Optional[dict] = {"$select": "id,title", "$top": "100"}
    while url:
        payload = _get(url, token, params=params)
        pages.extend(payload.get("value") or [])
        url = payload.get("@odata.nextLink")
        params = None  # nextLink already carries the full query string
    return pages


def html_to_text(html: str) -> str:
    """A OneNote page's content HTML, reduced to plain text.

    OneNote's export is layout markup — absolute-positioned `<div>`s per
    paragraph, no headings or lists that mean anything semantically — so
    there is nothing worth preserving structurally. `get_text` with newline
    separators, then dropping blank lines, gives the same loose free-text
    shape `import_external_recipe`'s prompt already expects from a pasted
    website or OCR'd photo.
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def page_content_text(token: str, page_id: str) -> str:
    response = requests.get(
        f"{GRAPH_ROOT}/me/onenote/pages/{page_id}/content",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    response.raise_for_status()
    return html_to_text(response.text)


def fetch_section_pages(token: str, section_name: str) -> List[Dict[str, str]]:
    """Every page in `section_name`, as `{title, body}` — the shape
    `onenote_import.import_pages` already knows how to walk, so fetching from
    Graph and fetching from a hand-copied `.txt` folder share one import
    loop.
    """
    section = find_section(token, section_name)
    entries = list_pages(token, section["id"])
    pages: List[Dict[str, str]] = []
    for entry in entries:
        title = (entry.get("title") or "").strip() or entry["id"]
        pages.append({"title": title, "body": page_content_text(token, entry["id"])})
    return pages


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Import every recipe page in a OneNote section into "
            "data/recipes_master.json, via the Microsoft Graph API."
        ),
    )
    parser.add_argument(
        "--section",
        required=True,
        help="Display name of the OneNote section to import, e.g. 'Fast 800 Program'.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the pages that would be imported without calling the parser.",
    )
    parser.add_argument(
        "--no-favorite",
        action="store_true",
        help=(
            "Add to the catalog without favoriting. Favoriting is the default "
            "because only favorites are eligible for slot pre-assignment."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-parse pages whose title already matches a catalog recipe.",
    )
    parser.add_argument(
        "--title",
        help=(
            "Only pages whose title contains this (case-insensitive). Pair "
            "with --force to redo one page without re-parsing the whole section."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Import at most this many pages — worth one pass before the full run.",
    )
    args = parser.parse_args(argv)

    if load_dotenv is not None:
        load_dotenv()

    configure_logging()

    try:
        token = acquire_token()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        pages = fetch_section_pages(token, args.section)
    except requests.HTTPError as exc:
        print(f"Graph request failed: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not pages:
        print(f"Section {args.section!r} has no pages.", file=sys.stderr)
        return 1

    if args.title:
        needle = args.title.strip().lower()
        pages = [p for p in pages if needle in p["title"].lower()]
        if not pages:
            print(f"No page title in {args.section!r} contains {args.title!r}.", file=sys.stderr)
            return 1
    if args.limit:
        pages = pages[: args.limit]

    print(f"{len(pages)} page(s) in {args.section!r}:")
    for page in pages:
        note = "  (empty)" if not page["body"] else ""
        print(f"  - {page['title']}{note}")

    if args.dry_run:
        print("\nDry run — nothing parsed, nothing written.")
        return 0

    key_error = api_key_error()
    if key_error:
        # Up front, not once per page: this is a misconfiguration that will
        # fail every call, and the per-page handler would turn it into one
        # identical failure per recipe after a long wait.
        print(key_error, file=sys.stderr)
        return 1

    repository = LocalJSONRepository()
    print()
    result = asyncio.run(
        import_pages(
            pages,
            repository,
            favorite=not args.no_favorite,
            force=args.force,
        )
    )

    print(
        f"\nImported {len(result['imported'])}, skipped {len(result['skipped'])}, "
        f"failed {len(result['failed'])}."
    )
    if result["failed"]:
        print("Failed pages (re-run with --force after fixing them in OneNote):")
        for title in result["failed"]:
            print(f"  - {title}")
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
