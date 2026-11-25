import { describe, expect, it } from "vitest";
import { detectBrowser, micProblem } from "./michelp";

describe("why Vesta cannot hear you, and where you are (E-237)", () => {
  it("knows the browser or app", () => {
    expect(detectBrowser("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Home Assistant/2026.10")).toBe("ha_ios");
    expect(detectBrowser("Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36 Home Assistant/2026.10 (io.homeassistant.companion.android)")).toBe("ha_android");
    expect(detectBrowser("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")).toBe("safari_ios");
    expect(detectBrowser("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/130.0 Mobile/15E148 Safari/604.1")).toBe("safari_ios");
    expect(detectBrowser("Mozilla/5.0 (Macintosh; Intel Mac OS X 15_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15")).toBe("safari_mac");
    expect(detectBrowser("Mozilla/5.0 (Macintosh; Intel Mac OS X 15_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36")).toBe("chrome");
    expect(detectBrowser("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 Edg/130.0")).toBe("edge");
    expect(detectBrowser("Mozilla/5.0 (Windows NT 10.0; rv:131.0) Gecko/20100101 Firefox/131.0")).toBe("firefox");
    expect(detectBrowser("Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36")).toBe("chrome_android");
    expect(detectBrowser("Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/26.0 Chrome/122.0 Mobile Safari/537.36")).toBe("samsung");
  });

  it("knows why the microphone did not open", () => {
    const error = (name: string) => Object.assign(new Error(name), { name });
    expect(micProblem(error("NotAllowedError"), false, null)).toBe("insecure");
    expect(micProblem(error("NotAllowedError"), true, "denied")).toBe("denied");
    expect(micProblem(error("NotAllowedError"), true, "granted")).toBe("frame");
    expect(micProblem(error("NotFoundError"), true, "prompt")).toBe("no_device");
    expect(micProblem(error("AbortError"), true, null)).toBe("other");
  });
});
