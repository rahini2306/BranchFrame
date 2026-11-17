# Upgrade verification — 18 November 2026

## Implemented

- Approximate edited canvas preview, clip trims/captions/mute/volume, incoming crossfades, three overlay tracks, and up to eight separate music/voiceover clips.
- FFmpeg composition exports with matching timeline scheduling, fade audio, overlays, soundtrack mixing, and a final audio limiter.
- Revisioned shared drafts, project presence, SSE updates, three-way reconciliation, and local-conflict recovery onto a new branch.
- Independent review approvals enforced on export requests; owner-configurable policy. New projects enable the gate by default.
- Email verification and password recovery through SMTP/SES; expiring hashed single-use tokens, reset session revocation, retryable delivery failure, and account rate limits.
- AWS configuration for generated CloudFront HTTPS, a VPC origin/internal ALB, EC2/EBS metadata, private S3, ECR, ECS Fargate workers, SES sender permissions, health alerts, and account-wide cost alerts.
- Portable VS Code workspace, updated API/architecture/deployment documentation, and a post-deployment smoke-test script.

## Checked for this upgrade

- 63 Python tests pass with FFmpeg installed. Coverage includes existing merge/ACL/upload/job/storage behavior plus account expiry/single-use/reset/revocation, delivery failure, verification enforcement, shared edits/conflicts, SSE payloads, independent approval gates, origin-header checks, and actual crossfade/overlay/music exports.
- Real rendering checks include both a two-clip crossfade and a cut followed by a crossfade, covering FFmpeg timebase normalization.
- All browser JavaScript files pass Node syntax checks. Python undefined-name/unused-import checks pass, HTML IDs are unique, and the Git diff has no whitespace errors.
- Terraform HCL parsing, dependency installation, and formatting pass. Full provider validation cannot run in this environment because provider subprocess/socket startup is restricted. This does not constitute a passing `terraform validate`.
- Ten editable Figma screens now include the multitrack studio and four account-recovery states. Structure audits confirm native text/frames/instances, the two product fonts, and no flattened whole-UI images. Synthetic sample content is used throughout. Editor and password-reset composition screenshots were visually inspected.

## Still pending

- The cloud browser is isolated from the local Python server, so the upgraded browser interaction/media playback has not received an end-to-end browser check here. The prior release's browser checks do not validate these new controls.
- AWS deployment is deferred at the project owner's request. No AWS resources were created and no live AWS website URL exists. Real SES delivery, cloud throughput, failover, backup restore, and charges have not been tested.
- The Docker image and upgraded Terraform configuration still need their CI checks on an unrestricted runner after publication.
- The upgraded source has now been synchronized into the existing Mac VS Code project folder. Local account data, media, and the virtual environment are retained.
- The earlier cloud-workspace push lacked authentication. Publication is now performed from the existing Mac checkout, with the remote commit checked after pushing.

Do not describe this as a tested AWS deployment or a production-ready public SaaS. The exact remaining engineering limits are in the README and AWS guide.
