# ADR 0010: The diagram and the demo are generated, and CI checks the diagram

**Status:** accepted · **Date:** 2026-10-24

## Context

The README needs an architecture diagram a reader takes in at a glance, and a
demo a reviewer can watch or run. Both tend to drift: a diagram drawn once by
hand stops matching the Terraform, and a screen recording stops matching the
code.

Options considered for the diagram:

| Option | Source | Kept in sync by |
| --- | --- | --- |
| Mermaid in the README (phases 1-5) | text in the README | review only; generic look, no AWS icons |
| draw.io or Excalidraw | a binary-ish file edited by hand | review only |
| Python `diagrams` | Python, needs Graphviz | its SVG links icons by local path, so it breaks on GitHub unless post-processed |
| **D2** | `docs/diagrams/architecture.d2`, plain text | CI renders it again and diffs the SVG |

For the recording: asciinema needs Linux or macOS, and this machine's WSL has
no distribution. VHS needs ttyd and ffmpeg, and works poorly on Windows.

## Decision

- **D2 0.9.0 draws the diagram.** It runs top-down with dagre, uses AWS icons
  embedded in the SVG, and follows the reader's light or dark theme.
  `make diagram` renders it. The README and `docs/architecture.md` show the
  SVG; the Mermaid block is gone, so there is one source.
- **CI checks it.** A `diagram is up to date` job downloads the same D2
  release (SHA-256 pinned), renders the source again, and fails if the
  committed SVG differs. Renders on Windows and on GitHub's Linux runners
  were byte-identical.
- **The demo is a script.** `scripts/demo.py` (`make demo`) runs the whole
  pipeline on LocalStack, times each step, and tears down.
- **The recording comes from the script itself.** `--record` writes an
  asciicast v2 file of exactly what it prints, as it prints it.
  `docs/demo/demo.cast` is a real run; `docs/demo/demo.gif` is that cast
  through agg 1.9.0 with idle time capped at 2 seconds. No terminal
  recorder is needed.

## Consequences

- **Icon fetching:** rendering fetches icons from `icons.terrastruct.com`,
  so the CI check needs network access. If the icon set ever changes, the
  check fails even though the source didn't; re-render and commit.
- **Re-recording:** the GIF and the cast go stale when the demo's output
  changes. Re-record with `python scripts/demo.py --record docs/demo/demo.cast`,
  then run agg. Nothing checks this automatically.
- **Contributor tools:** contributors need D2 0.9.0 to change the diagram.
  It is a single binary from the D2 releases; on Windows the tarball works
  without admin rights, where the MSI asks for elevation.
