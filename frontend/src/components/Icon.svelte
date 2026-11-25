<script lang="ts">
  // The line icons of the design system (24 grid, line 1.8, round ends, currentColor; H9,
  // E-260): one per tab and for the header, and the parts of the day (E-507). Always next to a word or with an aria-label.
  interface Props {
    name: string;
    size?: number;
  }
  let { name, size = 22 }: Props = $props();

  const paths: Record<string, string> = {
    home: "M3 11 12 4l9 7v9H3z",
    energy: "M13 2 4 14h7l-1 8 9-12h-7z",
    notifications: "M6 16V11a6 6 0 0 1 12 0v5l2 2H4z M10 21h4",
    brain: "M12 4a8 8 0 1 0 0 16a8 8 0 1 0 0-16z M8.5 13l2.5-3 2 2 2.5-3",
    conversation: "M4 5h16v11H10l-6 4z",
    logbook: "M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3z M5 17a3 3 0 0 1 3-3h11 M9 8h6",
    house_rules: "M6 3h12v18H6z M9 8h6 M9 12h6 M9 16h4",
    devices: "M9 3v5 M15 3v5 M7 8h10v4a5 5 0 0 1-10 0z M12 17v4",
    tidy: "M9 3h6v3H9z M7 4.5H5V21h14V4.5h-2 M8.5 13.5l2.5 2.5 4.5-5",
    test: "M9 3h6 M10 3v6l-5 9a2 2 0 0 0 1.7 3h10.6a2 2 0 0 0 1.7-3l-5-9V3 M7.5 15h9",
    more: "M5 12h.01 M12 12h.01 M19 12h.01",
    help: "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z M9.5 9.5a2.5 2.5 0 0 1 4.8 1c0 1.7-2.3 2-2.3 3.5 M12 17h.01",
    tour: "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z M15.5 8.5l-2 5-5 2 2-5z", // a compass: the tour (E-506)
    settings: "M12 9a3 3 0 1 0 0 6a3 3 0 1 0 0-6z M12 2.5v3 M12 18.5v3 M2.5 12h3 M18.5 12h3 M5.3 5.3l2.1 2.1 M16.6 16.6l2.1 2.1 M5.3 18.7l2.1-2.1 M16.6 7.4l2.1-2.1",
    person: "M12 4a4 4 0 1 0 0 8a4 4 0 1 0 0-8z M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6",
    search: "M11 4a7 7 0 1 0 0 14a7 7 0 1 0 0-14z M20 20l-4-4",
    filter: "M4 6h16 M7 12h10 M10 18h4",
    close: "M6 6l12 12 M18 6 6 18",
    chevron: "M9 6l6 6-6 6",
    export: "M12 15V3 M7 8l5-5 5 5 M5 14v6h14v-6",
    columns: "M4 5h16v14H4z M10 5v14 M15 5v14",
    // The parts of the day on the rhythm bar (E-507): sleeping, the morning, away; at home is "home".
    moon: "M19.5 14.5A8 8 0 1 1 9.5 4.5a6.5 6.5 0 0 0 10 10z",
    sunrise: "M3 19h18 M7 19a5 5 0 0 1 10 0 M12 9.5V4 M9.5 6.5 12 4l2.5 2.5 M4.9 12.9l1.4 1.4 M19.1 12.9l-1.4 1.4",
    // Straks: it gets dark, it gets light.
    sunset: "M3 19h18 M7 19a5 5 0 0 1 10 0 M12 4v5.5 M9.5 7 12 9.5 14.5 7 M4.9 12.9l1.4 1.4 M19.1 12.9l-1.4 1.4",
    sun: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z M12 2.5v2 M12 19.5v2 M2.5 12h2 M19.5 12h2 M5.3 5.3l1.4 1.4 M17.3 17.3l1.4 1.4 M5.3 18.7l1.4-1.4 M17.3 6.7l1.4-1.4",
    away: "M10 4H6.5A1.5 1.5 0 0 0 5 5.5v13A1.5 1.5 0 0 0 6.5 20H10 M14.5 8l4 4-4 4 M18.5 12H9",
  };
  const dots = $derived(name === "more");
</script>

<svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width={dots ? 3 : 1.8}
  stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <path d={paths[name] ?? paths.more} />
</svg>
