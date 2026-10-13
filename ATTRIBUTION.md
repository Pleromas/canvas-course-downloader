# Attribution

Canvas Vault is a fork. Credit where it is due:

- **Canvas Course Downloader & Exporter** by [jasp-nerd](https://github.com/jasp-nerd/canvas-course-downloader), MIT License.
  The extension architecture, Canvas API handling, content export, UI and most of the
  code in this repository are theirs. Canvas Vault started from v2.11.0.
- **canvas-teacher-export** by [dlxmax](https://github.com/dlxmax/canvas-teacher-export), MIT License.
  Rubric, quiz and teacher-data rendering in `downloader.js` and the YouTube/placeholder
  handling in `helpers.js` are ported from it (credited inline by the upstream author).
- **Turndown** and **turndown-plugin-gfm** by Dom Christie, MIT License (`turndown.min.js`,
  `turndown-plugin-gfm.min.js`), used for the Markdown export format.
- **client-zip** by David Junger, MIT License, was the original ZIP engine; it was replaced
  by `zipwriter.js` because Firefox content scripts cannot hand its ReadableStream output
  to the page realm.

Canvas Vault additions (Firefox support, schema-2 export manifest, `zipwriter.js`, the
`sync/` versioned archive, docs) are by Pleromas, also MIT. See `LICENSE`.
