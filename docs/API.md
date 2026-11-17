# API reference

All browser APIs use same-origin session cookies. JSON mutation endpoints use POST. Cross-origin POSTs with a mismatched Origin/Host are rejected. Most endpoints return JSON; media supports a single HTTP byte range. API errors return `{ "error": "message" }` with an appropriate status.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Server and local renderer status; remote workers are not inferred |
| POST | `/api/register` | Name, email, password; create session |
| POST | `/api/login` | Email, password; create session |
| POST | `/api/logout` | Revoke current session |
| GET | `/api/me` | Current user profile |
| GET / POST | `/api/projects` | List memberships / create project with name |
| GET | `/api/projects/:project` | Project snapshot including commits, branches, reviews, jobs |
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
