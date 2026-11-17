# Architecture and engineering decisions

## Data model

A project has members, source assets, immutable commits, branches, reviews, and exports. A commit stores its complete timeline manifest and zero, one, or two parent commit IDs. Branches point to commit IDs. Restoring an older version creates a new draft and commit; historical commits are never mutated.

Source assets are immutable. A clip references an asset plus trim boundaries, caption, and mute state. Footage is not copied when creating a branch. Branch names are local to a project. Assets are checked against the project before a commit is accepted.

Sessions use random tokens stored as SHA-256 hashes and HttpOnly SameSite cookies. Passwords use PBKDF2-HMAC-SHA256 with independent salts and 600,000 rounds. Every project API and media endpoint checks membership. Reviewer accounts can read and post reviews but cannot edit branches, commit, upload, invite, or export. Only owners can set teammate roles.

## Three-way merge

1. Traverse both commit DAGs, including both parents of previous merges.
2. Find the unique best common ancestor. Reject ambiguous criss-cross ancestry rather than inventing a base.
3. Match clips by stable identity. Compare fields against the base.
4. Keep identical changes; take a one-sided change when the other side is unchanged.
5. Report conflicting changes with a path, base, ours, and theirs. Delete/modify conflicts are atomic clip conflicts.
6. Compare the order of surviving shared clips separately. Independent inserts are placed deterministically using neighboring clip identities, with target-branch ordering taking precedence.
7. Detect invalid combinations such as a new start time greater than the other branch's new end time; require a whole-clip resolution.
8. Validate the final manifest, verify expected branch heads, insert a two-parent commit, and move the target head in one transaction.

The caller supplies expected heads for both branches. A changed branch invalidates a stale merge preview. Independent clip insertion uses a deterministic policy, not a universal conflict-free ordering algorithm. There is no CRDT or simultaneous live editing.

## Durable job orchestration

Each export creates one segment job per clip. A SQLite `BEGIN IMMEDIATE` transaction claims a job, generates a random lease token, increments attempts, and sets a 120-second lease. Workers renew it every 30 seconds.

The completion callback must match the running job's current lease and expected output object key. Stale workers cannot publish a completed job into metadata. This is **at-least-once execution**, not exactly-once execution: a worker can finish encoding after losing its lease. Immutable input specifications and deterministic output keys make duplicate processing safe at the metadata level. S3/local writes can still be repeated.

When all segments complete, the coordinator enqueues exactly one assembly job inside the same serialized transaction. Three failed attempts fail the export. Reclaiming abandoned jobs occurs when another worker requests work. No active worker means no lease sweep until a worker returns.

Step Functions is not required by this implementation: the application coordinator implements orchestration so lease behavior and failure recovery are visible and testable. AWS ECS supplies isolated rendering processes; it does not itself own the workflow state.

## Render cache

A segment cache key hashes the project ID, source content hash, trim range, caption, mute state, and renderer version. Clip IDs and branch names are excluded, allowing reuse across revisions within the same project. Project ID inclusion prevents cross-project cache reuse. Any encoding-setting change requires incrementing the renderer version.

All segments normalize to 1280×720, 30 fps, H.264/yuv420p, stereo AAC at 48 kHz, and a common video timebase. Missing or muted audio becomes silence. Captions are passed through a text file with expression expansion disabled. FFmpeg is invoked as an argument array, never a shell command. Final assembly stream-copies normalized segments.

## Upload integrity

The browser initiates or finds an upload session using filename, size, and a fingerprint. It hashes each 5 MiB part and checks the server's received hashes before skipping a part. Thus a resumed file with different content is not blindly mixed with prior parts. The server validates the expected part length and hash, atomically stores it, and assembles parts in order. Completion is idempotent and records a full-object SHA-256 hash. Staging is on the API host's local disk.

## Storage and scaling

Local mode uses a data directory containing SQLite and object files. Cloud mode keeps metadata on one EC2 host's EBS-backed disk and stores media in S3. Fargate workers talk to the API over the private VPC and access S3 with task roles. Worker credentials are limited to the application worker interface and read/write media prefixes.

The worker secret is shared across trusted workers; it is not an end-user credential. Traffic from workers to the API is HTTP inside the security-group-restricted VPC. Add internal TLS or mutual TLS if your threat model requires protection against observers inside the network.

A single API host is a deliberate first implementation. For high availability, move metadata and queue transactions to PostgreSQL, add stateless API replicas, and remove local upload staging. Do not mount SQLite WAL on EFS or let separate hosts independently open copies of its database.

## Measurement plan

For a cloud demo, record source duration/size, number of segments, worker count, cold/warm state, export wall time, cache hits, and failure/reclaim timestamps. Compare one worker with multiple workers using multiple clips; a one-clip export cannot demonstrate parallel segment speedup. Publish measured results and test conditions rather than theoretical speedups.
