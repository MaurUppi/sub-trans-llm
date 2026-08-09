# Issue tracker: Local Markdown

Issues and specs (you may know a spec as a PRD) for this repo live as Markdown files in `.scratch/`.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`
- The spec is `.scratch/<feature-slug>/spec.md`
- Implementation issues are one file per ticket at `.scratch/<feature-slug>/tickets/<NN>-<slug>.md`, numbered from `01` — never a single combined tickets file
- Triage state is recorded as a `Status:` line near the top of each new issue file (see `triage-labels.md` for the role strings)
- Existing tickets using `State:` and `Claim:` remain valid and should not be rewritten solely to adopt this configuration
- Comments and conversation history append to the bottom of the file under a `## Comments` heading

## When a skill says "publish to the issue tracker"

Create a new file under `.scratch/<feature-slug>/` (creating the directory if needed).

## When a skill says "fetch the relevant ticket"

Read the file at the referenced path. The user will normally pass the path or ticket number directly.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a file with one **child** file per ticket.

- **Map**: `.scratch/<effort>/map.md` — the Notes / Decisions-so-far / Fog body.
- **Child ticket**: `.scratch/<effort>/tickets/NN-<slug>.md`, numbered from `01`, with the question in the body. A `Type:` line records the ticket type (`research`/`prototype`/`grilling`/`task`); a `State:` line records whether it is open or resolved; a `Claim:` line records whether it is claimed.
- **Blocking**: a `Blockers:` line near the top. A ticket is unblocked when every file it lists is resolved.
- **Frontier**: scan `.scratch/<effort>/tickets/` for files that are open, unblocked, and unclaimed; first by number wins.
- **Claim**: set `Claim: claimed` and save before any work.
- **Resolve**: append the answer under an `## Answer` or `## Resolution` heading, set `State: resolved`, then append a context pointer (gist + link) to the map's Decisions-so-far in `map.md`.
