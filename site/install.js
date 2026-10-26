"use strict";

const BASE = "https://github.com/Mesmoraz/steadygate/releases/latest/download/";
const RELEASE_API = "https://api.github.com/repos/Mesmoraz/steadygate/releases/latest";
const ASSETS = {
  windows: "steadygate-setup-windows.exe",
  macos: "steadygate-setup-macos.zip",
  linux: "steadygate-setup-linux",
};
const LABELS = { windows: "Windows", macos: "Mac", linux: "Linux" };

function detectOS(nav) {
  const platform = (nav && nav.platform) || "";
  const ua = (nav && nav.userAgent) || "";
  if (/android|iphone|ipad|ipod/i.test(ua)) return null;
  if (/^win/i.test(platform)) return "windows";
  if (/^mac/i.test(platform)) return "macos";
  if (/linux/i.test(platform)) return "linux";
  if (/windows/i.test(ua)) return "windows";
  if (/macintosh|mac os x/i.test(ua)) return "macos";
  if (/linux|x11/i.test(ua)) return "linux";
  return null;
}

function downloadUrl(os) {
  return BASE + ASSETS[os];
}

async function initInstall(doc, fetchFn) {
  const btn = doc.getElementById("install-now");
  if (!btn) return;
  const view = doc.defaultView || {};
  const nav = view.navigator || {};
  const uaData = nav.userAgentData;
  const os = detectOS({
    platform: (uaData && uaData.platform) || nav.platform || "",
    userAgent: nav.userAgent || "",
  });
  if (os) {
    btn.href = downloadUrl(os);
    btn.textContent = "Install for " + LABELS[os];
  } else {
    btn.href = "#install";
    btn.textContent = "Install now";
  }
  try {
    const res = await fetchFn(RELEASE_API, { headers: { Accept: "application/vnd.github+json" } });
    if (res && !res.ok && res.status === 404) {
      btn.href = "#install";
      btn.className = "install-soon";
      btn.innerHTML =
        "Coming soon. For now: <code>pip install git+https://github.com/Mesmoraz/steadygate</code>";
    }
  } catch (err) {
    // Offline, rate-limited or blocked: keep the download link as it is.
  }
}

if (typeof window !== "undefined" && typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", () => initInstall(document, window.fetch.bind(window)));
}

if (typeof module !== "undefined") {
  module.exports = { ASSETS, LABELS, RELEASE_API, detectOS, downloadUrl, initInstall };
}
