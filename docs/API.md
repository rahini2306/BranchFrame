# API reference

All browser APIs use same-origin session cookies. JSON mutation endpoints use POST. Cross-origin POSTs with a mismatched Origin/Host are rejected. Most endpoints return JSON; media supports a single HTTP byte range. API errors return `{ "error": "message" }` with an appropriate status.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Server and local renderer status; remote workers are not inferred |
| POST | `/api/register` | Name, email, password; create session |
| POST | `/api/login` | Email, password; create session |
| POST | `/api/request-verification` | Authenticated resend; delivery must be configured |
| POST | `/api/verify-email` | Consume `token` once |
| POST | `/api/request-password-reset` | Generic response for known/unknown `email` |
| POST | `/api/reset-password` | Consume `token`, update `password`, revoke all sessions |
| POST | `/api/logout` | Revoke current session |
| GET | `/api/me` | Current user profile |
| GET / POST | `/api/projects` | List memberships / create project with name |
| GET | `/api/projects/:project` | Project snapshot including commits, branches, reviews, jobs |
| GET / POST | `/api/projects/:project/draft` | Read by `?branch=main`; save `branch`, `revision`, `timeline` with three-way reconciliation |
| GET | `/api/projects/:project/events?branch=main` | Authenticated SSE: revision, head, presence, approval; heartbeat every 2s |
| POST | `/api/projects/:project/settings` | Owner sets boolean `require_approval` |
| POST | `/api/projects/:project/members` | Owner sets registered email's editor/reviewer role |
| POST | `/api/projects/:project/branches` | Create branch with `name` and commit `head` |
| POST | `/api/projects/:project/commits` | `branch`, `expected_head`, `timeline`, `message` |
| GET | `/api/projects/:project/diff?before=...&after=...` | Revision differences |
| POST | `/api/projects/:project/merge` | Preview or apply a three-way merge |
| POST | `/api/projects/:project/reviews` | `commit`, `kind`, `body`, `at` |
| POST | `/api/projects/:project/renders` | Queue export of `commit` |
| POST | `/api/projects/:project/uploads` | Start/resume session with `name`, `size`, `fingerprint` |
| GET | `/api/projects/:project/uploads/:upload` | Received part hashes and completion state |
| POST | `/api/projects/:project/uploads/:upload/parts/:index` | Raw bytes; `X-Part-SHA256` header |
| POST | `/api/projects/:project/uploads/:upload/complete` | Idempotent assembly into an asset |
| POST | `/api/projects/:project/assets` | Legacy whole-file upload; raw bytes and URL-encoded `X-Filename` |
| GET | `/api/projects/:project/assets/:asset/media` | Authorized source playback |
| GET | `/api/projects/:project/renders/:render/media` | Authorized finished export playback/download |

## Timeline manifest

```json
{
  "clips": [
    {
      "id": "stable-clip-id",
      "asset": "32-character-project-asset-id",
      "start": 0,
      "end": 4.5,
      "caption": "Opening shot",
      "muted": false
    }
  ]
}
```

`asset` must be an actual hexadecimal asset ID in the project. Maximum 100 clips and one hour total timeline duration. Source duration is checked during rendering, so an invalid source trim can be committed but fails its export with a diagnostic message.

## Merge request

```json
{
  "target": "main",
  "source": "social",
  "expected_target": "CURRENT_TARGET_COMMIT_ID",
  "expected_source": "CURRENT_SOURCE_COMMIT_ID",
  "apply": false,
  "resolutions": {}
}
```

A preview returns `timeline`, `conflicts`, and `base`. Resolutions map paths such as `clips/CLIP_ID/caption`, `clips/CLIP_ID`, or `order` to `ours` or `theirs`. Set `apply=true` to create a two-parent merge commit once every conflict is resolved. Head changes return HTTP 409.

## Worker API

POST `/internal/claim`, `/internal/heartbeat`, and `/internal/finish` require `Authorization: Bearer <BRANCHFRAME_WORKER_TOKEN>`. Claim returns a job or null. Heartbeat uses `id` and `lease`; finish also includes `result` (expected object key) or `error`. These are trusted-worker endpoints, not user APIs. Put remote workers in the restricted VPC and keep the worker secret out of logs and browsers.

## Editing and review extensions

A video clip may include `track` (integer 0–3), `volume` (0–2), `transition` (`cut` or `fade`), `transition_duration` (0.05–2 seconds), and overlay `at`, `x`, `y`, `width`, `height`. Track 0 is sequential; tracks 1–3 overlay at the specified absolute time. Overlay bounds are normalized to the 1280×720 output and must fit the frame. Incoming fades are capped to half of either adjoining clip. Exports require at least one main-track clip.

The manifest may contain `audio`, up to eight entries with required `id`, `asset`, `start`, `end`, `at`, `volume`, and optional `fade_in`/`fade_out`. Soundtracks merge by stable entry identity; conflicting edits use `audio/ID` resolutions. Audio trims must fit the source and placement does not extend main video duration.

New projects default to `require_approval=true`; owners may explicitly choose false. Project snapshots include `require_approval` and an `approval` map keyed by commit ID, containing `required`, `allowed`, `approvals`, and `blocking`. The author cannot approve their own commit. Each independent reviewer's latest decision applies; any outstanding change request blocks a new export. A gate rejection returns HTTP 403.

Draft saves return the merged shared manifest and new revision. Conflicts or expired history return 409 without discarding either the stored draft or the caller's local copy. A commit must match the current shared manifest when a draft exists. Saves, commits, and merges are checked transactionally.

Email delivery may return 503 if unconfigured or failing, and account endpoints may return 429 on repeated attempts. Verification enforcement is configured through `BRANCHFRAME_REQUIRE_VERIFIED_EMAIL=1`; the cloud bootstrap enables it. Tokens go in request bodies, never query strings. Do not record token-bearing mail bodies or URL fragments in telemetry.
