---
status: ACTIVE
owner: maintainers
applies_to_commit: 85bac85356d8092adfe98cd82ee59f81a242cf53
last_verified: 2026-09-02
---

# Project Brand Guide

`qlib-platform` uses a deliberately restrained visual system: engineering-first, research-oriented, and readable in both light and dark contexts. Brand assets should support technical comprehension rather than compete with it.

## Assets

| Asset | Path | Intended use |
| --- | --- | --- |
| Project mark | `docs/assets/brand/qlib-platform-mark.svg` | favicon, compact icon, documentation header |
| Project wordmark | `docs/assets/brand/qlib-platform-logo.svg` | README/docs hero, presentations, repository graphics |
| System overview | `docs/assets/architecture/system-overview.svg` | architecture orientation and onboarding |

SVG is the canonical source format because it remains crisp at different sizes and is easy to review in Git history. Do not commit generated raster copies unless a destination specifically requires PNG/JPEG.

## Meaning of the mark

The mark combines three ideas:

- a **Q-shaped research loop** for Qlib and iterative quantitative research;
- connected **signal/data nodes** representing immutable lineage and evidence;
- an **outgoing tail/arrow** representing the governed target-portfolio handoff from the Research Plane to the separate Execution Plane.

The mark should not imply broker execution ownership. The repository remains a Research Plane.

## Core palette

| Token | Hex | Use |
| --- | --- | --- |
| Ink | `#111827` | primary dark surface / strong text |
| Research indigo | `#312E81` | core brand depth |
| Signal blue | `#60A5FA` | intermediate signal/data nodes |
| Evidence cyan | `#67E8F9` | data/evidence highlight |
| Model violet | `#A78BFA` | model/research stage |
| Handoff magenta | `#E879F9` | outbound governed handoff accent |
| Slate | `#64748B` | secondary text and connectors |
| Paper | `#F8FAFC` | light technical canvas |

Use color semantically and sparingly. Architecture and research diagrams should remain understandable when printed or viewed by users with reduced color discrimination; labels and structure must carry meaning independently of color.

## Typography

The documentation site prefers:

- **Inter** for interface and prose when available;
- **JetBrains Mono** for code when available;
- system sans/monospace fallbacks when those fonts are not installed.

The repository does not vendor font binaries. GitHub README rendering should rely on GitHub-native typography rather than CSS hacks or external font dependencies.

## Layout principles

- Put the value proposition before implementation detail.
- Prefer one strong diagram over several decorative graphics.
- Keep large blocks of governance text in dedicated documentation, not in the README hero.
- Use whitespace, tables, and callouts to establish hierarchy before introducing more color.
- Keep diagrams editable as source (`.svg` or Mermaid) and pair static diagrams with normative text links.

## Diagram policy

Static diagrams are orientation aids. They are **not** normative contracts.

For example, the system overview visually explains the main pipeline, but ownership and identity rules remain defined by:

- [Architecture Boundary](../architecture_boundary.md)
- [Identity and Lineage](../identity_and_lineage.md)
- [Current State](../current_state.md) for moving governance facts

When behavior changes, update both the normative document and any affected visual in the same pull request.

## External use

The project name and original brand artwork may be used to refer accurately to the project. Do not use the logo in a way that implies endorsement, certification, investment performance, or an official relationship with Microsoft/Qlib, TuShare, a broker, or another third party beyond what the repository explicitly states.
