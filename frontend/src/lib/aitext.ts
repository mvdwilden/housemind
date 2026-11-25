// What went to an AI and came back, readable (E-169, E-348): the data HouseMind sends sits in
// <data>…</data> as JSON; shown with indents. Anything else stays as it is.

export function readable(text: string): string {
  const body = text.replace(/^[\s\S]*?<data>\s*/, "").replace(/\s*<\/data>[\s\S]*$/, "");
  try {
    return JSON.stringify(JSON.parse(body), null, 2);
  } catch {
    return text;
  }
}
