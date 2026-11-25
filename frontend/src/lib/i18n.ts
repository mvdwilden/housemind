// Texts come from the language files on the server (E-24). English fallback (E-26).

export type Texts = Record<string, string>;

export function text(
  texts: Texts,
  fallback: Texts,
  key: string,
  params: Record<string, string | number> = {},
  count?: number,
): string {
  let lookup = key;
  if (count !== undefined) {
    lookup = count === 1 ? `${key}.one` : `${key}.other`;
    params = { count, ...params };
  }
  const template = texts[lookup] ?? fallback[lookup] ?? lookup;
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in params ? String(params[name]) : match,
  );
}
