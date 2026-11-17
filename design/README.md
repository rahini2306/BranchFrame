# Pixel Playground design

[Figma — BranchFrame Pixel Playground](https://www.figma.com/design/OTbmffP6Old0d3wdNRsOqv/BranchFrame-Pixel-Playground?node-id=3-15)

Ten editable screens: desktop landing, mobile landing, workspace dashboard, upgraded multitrack timeline editor, sign in, compare/merge, reset request, new password, email verified, and invalid link. The file contains reusable button, project-card, and toolkit-card components, plus bound color variables and a token reference board.

The source follows the user-provided reference's pink surround, pale page, terminal-style pixel headlines, green flower doodles, clouds, and skateboard mascot. The reference was too low-resolution to verify an exact font; **VT323** is the selected close match. **Space Grotesk** supplies readable UI text. Both are self-hosted with OFL license files in `web/fonts/`.

| Token | Value |
|---|---|
| Pink | `#f14b9e` |
| Illustration green | `#39ae4a` |
| Ink | `#111111` |
| Paper | `#f1f1ef` |
| Muted text | `#62645f` |
| Border | `#cfd0ca` |

`figma.json` records the file URL and exact screen IDs. `assets.json` mirrors the original SVG assets in `web/art/`. The Figma scripts document construction of the specific file; they are not application runtime code. Generated Figma node IDs are file-specific. Preview PNGs are exports of the editable design, not captures of live accounts or footage.

All Figma project names, timeline content, and metadata are synthetic. The live authenticated workspace was not imported. The designs describe visual direction; the browser application implements working account, versioning, upload, and render behavior. The Figma file does not claim to execute real video processing.
