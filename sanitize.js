/**
 * Canvas Vault — filename / path sanitising shared by the background script
 * and the content scripts.
 *
 * Loaded by the background (event page `scripts` list on Firefox,
 * `importScripts` in Chrome's service worker) and as a content script, so it
 * must stay free of DOM and extension API use. Pure functions only.
 */

const _EXOTIC_SPACES = /[\u00A0\u1680\u2000-\u200A\u202F\u205F\u3000]/g;
const _INVISIBLE = /[\u0000-\u001F\u007F-\u009F\u200B-\u200F\u2028-\u202E\u2060-\u206F\uFEFF]/g;
const _RESERVED = /[/\\?%*:|"<>]/g;
const _WIN_DEVICES = /^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)/i;

/**
 * One path component (folder or file name) made safe for every OS Firefox and
 * Chrome download to. Exotic Unicode spaces become plain spaces (macOS
 * screenshots carry U+202F before "AM"), control/invisible characters are
 * dropped, OS-reserved characters become "-", leading dots and trailing
 * dots/spaces go, Windows device names get a prefix. Empty input yields
 * "untitled" so a path never collapses.
 */
function sanitizePathSegment(name) {
  const cleaned = String(name ?? "")
    .replace(_EXOTIC_SPACES, " ")
    .replace(_INVISIBLE, "")
    .replace(_RESERVED, "-")
    .replace(/^\.+/, "")
    .replace(/[. ]+$/, "")
    .replace(_WIN_DEVICES, "_$1$2")
    .trim();
  return cleaned || "untitled";
}

/**
 * Whole relative download path: split on either slash, sanitise each segment,
 * drop empty / "." / ".." segments, rejoin with "/". Never starts with "/".
 */
function sanitizeDownloadPath(p) {
  return String(p ?? "")
    .split(/[\\/]+/)
    .filter((seg) => seg !== "" && seg !== "." && seg !== "..")
    .map(sanitizePathSegment)
    .join("/");
}
