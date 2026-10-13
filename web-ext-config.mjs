// web-ext configuration: `npx web-ext lint|build|sign` picks this up automatically.
// Everything that is not part of the shipped extension stays out of the XPI.
export default {
  ignoreFiles: [
    ".git", ".github", ".gitignore", ".superpowers",
    "sync", "docs", "tests", "screenshots",
    "web-ext-artifacts", "web-ext-config.mjs", "node_modules",
    "CLAUDE.md", "CONTRIBUTING.md", "README.md", "PRIVACY.md",
    "*.md", "*.zip", "*.xpi", "*.pem", "*.crx", "package*.json",
  ],
  build: { overwriteDest: true },
  sign: { channel: "unlisted" },
};
