// Extract JSON objects from Wrangler's pretty-printed JSON stream and text preamble.
export function jsonStream(onObject) {
  let depth = 0, quoted = false, escaped = false, buffer = '';
  return chunk => {
    for (const c of chunk) {
      if (!depth) { if (c !== '{') continue; depth = 1; buffer = '{'; continue; }
      buffer += c;
      if (quoted) {
        if (escaped) escaped = false;
        else if (c === '\\') escaped = true;
        else if (c === '"') quoted = false;
      } else if (c === '"') quoted = true;
      else if (c === '{') depth++;
      else if (c === '}' && --depth === 0) {
        try { onObject(JSON.parse(buffer)); } catch {}
        buffer = '';
      }
    }
  };
}
