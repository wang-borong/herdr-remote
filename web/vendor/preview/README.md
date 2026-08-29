# Workspace preview libraries

These browser builds are vendored so the authenticated Herdr UI never loads
scripts from a CDN.

- Marked 18.0.10 (`marked-18.0.10.js`) — Markdown parsing, MIT
- DOMPurify 3.4.14 (`dompurify-3.4.14.min.js`) — HTML sanitizing, Apache-2.0 or MPL-2.0
- Highlight.js 11.12.0 (`highlight-11.12.0.min.js`) — syntax highlighting, BSD-3-Clause

The files are unmodified release artifacts from their corresponding npm
packages, apart from a final newline added by the repository patch tooling.
The license texts are stored beside the browser builds.
