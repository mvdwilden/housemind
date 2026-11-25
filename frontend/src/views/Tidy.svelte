<script lang="ts">
  // Home Assistant netjes as a page of its own (E-449, B-363): in the menu for an administrator
  // on Expert, with room for its long lists – no longer a group in House settings.
  import TidyGroup from "../components/TidyGroup.svelte";
  import type { TidyView } from "../lib/types";
  import type { T } from "../lib/ui";

  interface Props {
    tidy: TidyView | undefined;
    lang: string;
    t: T;
    send: (message: Record<string, unknown>) => void;
  }
  let { tidy, lang, t, send }: Props = $props();
</script>

<h2>{t("tabs.tidy")}</h2>
<TidyGroup {tidy} {lang} {t} {send} />

<style>
  h2 { margin: 0; font-family: var(--font-display); font-size: calc(20px * var(--text-scale)); }
</style>
