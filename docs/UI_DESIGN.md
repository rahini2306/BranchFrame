# UI and Figma handoff

Design a desktop-first collaborative video workspace, then adapt it for tablet and mobile. The current UI is a functional baseline in `web/index.html`, `web/style.css`, and `web/app.js`.

## Screens

| Screen | Content and interactions |
|---|---|
| Sign in / register | Email, password, name on registration, validation feedback |
| Project dashboard | Project cards, role labels, create-project dialog, empty state |
| Timeline studio | Branch selector, media uploads, source player, clip list, trim fields, captions, mute, reorder, commit |
| Version history | Commit IDs, parent references, branch labels, timeline inspection, differences, restore-to-draft, branch-from-revision |
| Compare and merge | Source/target branches, changes, base/ours/theirs conflicts, resolution controls, merge result |
| Review room | Timestamped comment, approve/request-changes type, exact commit reference |
| Exports | Queued/running/failed/done states, task attempts, cache count, playback and download |
| Team | Existing members, editor/reviewer roles, invitation by registered email |

## States that need designs

- Empty projects, media library, timeline, review list, and export history.
- Upload progress and recoverable interrupted upload.
- Unsaved timeline, stale branch conflict, invalid trim, unauthorized action.
- Failed segment, retry attempt, completed export, cache reuse.
- Disabled editing controls for reviewers.
- Desktop layout around 1440 px; narrow layout around 390 px.

## Useful design references

Search for collaborative video editor dashboards, Git branch history interfaces, version comparison panels, media asset libraries, and job processing dashboards. Focus on the application workspace rather than only a cloud-company landing page.

Keep original-source preview distinct from rendered export playback. Do not imply the browser player applies edits in real time. Reviews reference commits; they are not a live chat. Branch merges need explicit conflict choices, not an unexplained success button.

Deliver a Figma link plus chosen fonts/colors, reusable controls, screen states, and desktop/mobile frames. The CSS uses variables at the top for easy restyling. No design is required before running or testing the backend.
