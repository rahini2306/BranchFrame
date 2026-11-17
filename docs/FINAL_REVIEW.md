# Final verification

## Verified locally

- Complete source is saved in the repository; `BranchFrame.code-workspace` opens that folder in VS Code.
- Desktop landing at 1440 px and mobile landing/workspace/editor at 390 px checked in the browser. Document width matches viewport width; the clip table scrolls inside its own panel.
- The reference-inspired fonts, pink/green palette, original SVG decorations, and responsive treatment are applied to the landing page and application.
- Browser flow: open studio → load project → export committed timeline → reuse a cached segment → assemble → finished MP4 download.
- All 44 Python tests pass. The suite covers merge semantics, permissions, stale commits, job leases/retries, cache reuse, upload integrity/resume, atomic object storage, cleanup ordering, and real FFmpeg encoding/assembly.
- Frontend JavaScript syntax and Python unused-import checks pass.
- Terraform formatting passes. GitHub Actions validates the Terraform provider configuration and builds the Docker image.

## Reliability fixes made during review

1. Object copies/downloads publish through an atomic rename, preserving complete prior objects and avoiding partially cached downloads.
2. Uploads retain a stable project URL and block project/branch navigation while sending parts; progress is displayed inline.
3. Completed asset metadata commits before staged upload parts are removed, preserving recoverability if the transaction fails.
4. Background status updates do not replace a playing video; pending UI updates are applied after playback stops.
5. Creating a branch from history checks for uncommitted edits before replacing a draft.

## Design verification

Six screens exist in the separate Figma file. Audits confirm editable text, vector artwork, component instances, and the two selected font families. Whole-screen raster images are not used as editable design substitutes. Sample projects in the design file contain no live account or uploaded media data.

## Remaining validation and scope

The cloud architecture has not been applied to an AWS account. Real IAM/service behavior, cloud costs, recovery from EBS backups, and multi-host throughput must be tested before claiming a production deployment or measured scalability. The README documents the single coordinator, advisory reviews, source-only preview, and missing public-SaaS account hardening.

Use these concrete checks and scope limits when presenting the project. Cloud performance and production readiness require additional live validation.
