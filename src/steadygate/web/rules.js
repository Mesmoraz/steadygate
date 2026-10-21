// Merge a suggested rule into steadygate.yml text without disturbing the rest of the file:
// comments, other rules and the matched rule's other keys (such as `required`) all stay.
"use strict";

const ITEM = /^(\s*)-\s+type:\s*(\S+)/;

function ruleYaml(rule, indent = "  ") {
  return [
    `${indent}- type: ${rule.type}`,
    `${indent}  metric: ${rule.metric}`,
    `${indent}  direction: ${rule.direction}`,
    `${indent}  max_regression: ${rule.max_regression}`,
  ].join("\n");
}

function indentOf(line) {
  return line.match(/^\s*/)[0].length;
}

// One past the last line of the rule item starting at `start`.
function blockEnd(lines, start) {
  const depth = indentOf(lines[start]);
  let j = start + 1;
  while (j < lines.length) {
    const line = lines[j];
    if (line.trim() !== "" && (indentOf(line) <= depth || ITEM.test(line))) break;
    j++;
  }
  while (j > start + 1 && lines[j - 1].trim() === "") j--;
  return j;
}

function mergeRule(yamlText, rule) {
  const lines = yamlText.replace(/\s+$/, "").split("\n");
  const starts = [];
  lines.forEach((line, i) => {
    if (ITEM.test(line)) starts.push(i);
  });

  for (const s of starts) {
    const e = blockEnd(lines, s);
    const block = lines.slice(s, e);
    const isDelta = ITEM.exec(lines[s])[2] === rule.type;
    const metricAt = block.findIndex((l) => {
      const m = l.match(/^\s*(?:-\s+)?metric:\s*(\S+)\s*$/);
      return m !== null && m[1] === rule.metric;
    });
    if (!isDelta || metricAt < 0) continue;

    const keyIndent = " ".repeat(indentOf(lines[s]) + 2);
    let insertAt = s + metricAt + 1;
    for (const key of ["direction", "max_regression"]) {
      // Re-read the block each time: an insert for the previous key shifted the lines.
      const current = lines.slice(s, blockEnd(lines, s));
      const at = current.findIndex((l) => new RegExp(`^\\s*${key}:`).test(l));
      if (at >= 0) {
        lines[s + at] = lines[s + at].replace(/:.*$/, `: ${rule[key]}`);
      } else {
        lines.splice(insertAt, 0, `${keyIndent}${key}: ${rule[key]}`);
        insertAt++;
      }
    }
    return lines.join("\n") + "\n";
  }

  const indent = starts.length ? " ".repeat(indentOf(lines[starts[0]])) : "  ";
  const added = ruleYaml(rule, indent).split("\n");
  if (starts.length) {
    lines.splice(blockEnd(lines, starts[starts.length - 1]), 0, ...added);
  } else {
    if (!lines.some((l) => /^rules:/.test(l))) lines.push("rules:");
    lines.push(...added);
  }
  return lines.join("\n") + "\n";
}

if (typeof module !== "undefined") module.exports = { mergeRule, ruleYaml };
