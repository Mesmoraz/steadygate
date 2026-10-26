"use strict";

// Renders exactly what steadygate.report.markdown emits. All text is HTML-escaped first;
// only the tag shapes listed below are ever produced.

function escapeHtml(s) {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Escape first; a code span's content is then already inert.
function inline(text) {
  return escapeHtml(text).replace(/`([^`]*)`/g, "<code>$1</code>");
}

function splitRow(line) {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  return s.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
}

function isSeparator(line) {
  return /^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/.test(line.trim());
}

function renderMarkdown(md) {
  const lines = md.split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    let m;
    if (line.trim() === "") {
      i++;
    } else if ((m = /^### (.*)$/.exec(line))) {
      out.push("<h3>" + inline(m[1]) + "</h3>");
      i++;
    } else if (line.startsWith("|")) {
      const rows = [];
      while (i < lines.length && lines[i].startsWith("|")) {
        if (!isSeparator(lines[i])) rows.push(splitRow(lines[i]));
        i++;
      }
      const cell = (tag, c) => "<" + tag + ">" + inline(c) + "</" + tag + ">";
      let html = "<table>";
      if (rows.length) {
        html += "<thead><tr>" + rows[0].map((c) => cell("th", c)).join("") + "</tr></thead><tbody>";
        for (const r of rows.slice(1)) html += "<tr>" + r.map((c) => cell("td", c)).join("") + "</tr>";
        html += "</tbody>";
      }
      out.push(html + "</table>");
    } else if (line.startsWith("- ")) {
      const items = [];
      while (i < lines.length && lines[i].startsWith("- ")) {
        items.push("<li>" + inline(lines[i].slice(2).replace(/\\\|/g, "|")) + "</li>");
        i++;
      }
      out.push("<ul>" + items.join("") + "</ul>");
    } else if ((m = /^<details><summary>(.*)<\/summary>$/.exec(line))) {
      out.push("<details><summary>" + inline(m[1]) + "</summary>");
      i++;
    } else if (line.trim() === "</details>") {
      out.push("</details>");
      i++;
    } else if ((m = /^<sub>(.*)<\/sub>$/.exec(line))) {
      out.push("<sub>" + inline(m[1]) + "</sub>");
      i++;
    } else {
      out.push("<p>" + inline(line) + "</p>");
      i++;
    }
  }
  return out.join("\n");
}

if (typeof module !== "undefined") module.exports = { renderMarkdown, escapeHtml };
