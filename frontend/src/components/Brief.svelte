<script lang="ts">
  // A short confirmation such as "Bewaard" (E-174, wish from the real house): plain text in the
  // header, next to the round buttons – no box, no Oké, no bar. It goes by itself after its time;
  // on "Lang" (E-299) it stays until it is tapped away, so then it is a button. A screen reader
  // hears it through the one status line in the app.
  interface Props {
    text: string;
    tappable: boolean; // "Lang": stays until tapped
    floating?: boolean; // scrolled down: at the top right over the page
    onclose: () => void;
  }
  let { text, tappable, floating = false, onclose }: Props = $props();
</script>

{#if tappable}
  <button class="brief" class:floating onclick={onclose}>{text}</button>
{:else}
  <span class="brief" class:floating aria-hidden="true">{text}</span>
{/if}

<style>
  .brief {
    align-self: center; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    font-size: calc(14px * var(--text-scale)); font-weight: 600; color: var(--muted); padding: 0 4px;
    border: none; background: none; font-family: inherit;
  }
  button.brief { min-height: 44px; cursor: pointer; }
  .floating {
    position: absolute; top: 10px; right: 16px; z-index: 5; padding: 8px 14px; border-radius: 18px;
    background: var(--surface); border: 1px solid var(--line); box-shadow: 0 4px 16px rgba(0, 0, 0, 0.18);
  }
  /* A phone has no room next to the buttons: just under them, over the page for a moment,
     so "Alles rustig" keeps its width. */
  @media (max-width: 560px) {
    .brief:not(.floating) {
      position: absolute; top: 60px; right: 16px; z-index: 5; max-width: 70vw; padding: 8px 14px;
      border-radius: 18px; background: var(--surface); border: 1px solid var(--line);
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.18);
    }
  }
</style>
