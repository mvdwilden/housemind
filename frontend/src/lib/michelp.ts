// When Vesta cannot hear you (E-237, B-85): why not, and where you are – so the Vesta window
// can say exactly how to switch the microphone on, for this browser or app.

export type Browser =
  | "ha_ios"
  | "ha_android"
  | "safari_ios"
  | "safari_mac"
  | "chrome_android"
  | "samsung"
  | "edge"
  | "firefox"
  | "chrome"
  | "other";

export type MicProblem = "insecure" | "denied" | "frame" | "no_device" | "other";

/** The browser or app, from what the browser says about itself. */
export function detectBrowser(userAgent: string): Browser {
  const ua = userAgent.toLowerCase();
  const ios = /iphone|ipad|ipod/.test(ua) || (ua.includes("macintosh") && ua.includes("mobile"));
  const android = ua.includes("android");
  if (ua.includes("home assistant") || ua.includes("homeassistant")) {
    return android ? "ha_android" : "ha_ios";
  }
  if (ua.includes("samsungbrowser")) return "samsung";
  if (ua.includes("edg/") || ua.includes("edga/") || ua.includes("edgios/")) return "edge";
  if (ua.includes("firefox/") || ua.includes("fxios/")) return "firefox";
  if (ios) return "safari_ios"; // on iPhone and iPad every browser follows Safari's rules
  if (android && ua.includes("chrome/")) return "chrome_android";
  if (ua.includes("chrome/") || ua.includes("crios/")) return "chrome";
  if (ua.includes("safari/")) return "safari_mac";
  return "other";
}

/** Why the microphone did not open. ``permission`` is what the browser's permission check
 * says ("granted", "denied", "prompt" or null when it cannot tell). */
export function micProblem(error: unknown, secure: boolean, permission: string | null): MicProblem {
  if (!secure) return "insecure";
  const name = error instanceof Error || (typeof error === "object" && error !== null) ? String((error as { name?: string }).name ?? "") : "";
  if (name === "NotFoundError" || name === "OverconstrainedError") return "no_device";
  if (name === "NotAllowedError" || name === "SecurityError" || name === "PermissionDeniedError") {
    // Allowed for the site, yet refused: the page around HouseMind does not let it listen.
    return permission === "granted" || permission === "prompt" ? "frame" : "denied";
  }
  return "other";
}

/** What the browser's permission check says about the microphone, when it can tell. */
export async function micPermission(): Promise<string | null> {
  try {
    const status = await navigator.permissions.query({ name: "microphone" as PermissionName });
    return status.state;
  } catch {
    return null;
  }
}
