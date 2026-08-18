/** The device label in the session list.
 *
 *  Its whole job is to let somebody recognise their own laptop in a list, so the
 *  cases that matter are the ones where a browser lies about what it is. Chrome
 *  and Edge both claim to be Safari, Edge also claims to be Chrome, and the
 *  order of the checks is the only thing keeping those apart — which is exactly
 *  the kind of thing that gets reordered by someone tidying up.
 */
import { describe, expect, it } from "vitest";

import { describeDevice } from "./AdminScreens";

describe("describeDevice", () => {
  it("does not call Chrome 'Safari', though Chrome says it is one", () => {
    const chromeOnWindows =
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) " +
      "Chrome/120.0.0.0 Safari/537.36";
    expect(describeDevice(chromeOnWindows)).toBe("Chrome on Windows");
  });

  it("does not call Edge 'Chrome', though Edge says it is one", () => {
    const edge =
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) " +
      "Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0";
    expect(describeDevice(edge)).toBe("Edge on Windows");
  });

  it("recognises real Safari, which is the one with no Chrome in it", () => {
    const safari =
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) " +
      "Version/17.0 Safari/605.1.15";
    expect(describeDevice(safari)).toBe("Safari on macOS");
  });

  it("names the phone, because that is the one people check for", () => {
    const iphone =
      "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 " +
      "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1";
    expect(describeDevice(iphone)).toBe("Safari on iOS");

    const android =
      "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) " +
      "Chrome/120.0.0.0 Mobile Safari/537.36";
    // Android before Linux — an Android UA contains both, and "Chrome on Linux"
    // for somebody's phone is the wrong answer given confidently.
    expect(describeDevice(android)).toBe("Chrome on Android");
  });

  it("recognises Firefox on desktop and on iOS", () => {
    expect(describeDevice("Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0"))
      .toBe("Firefox on Linux");
    expect(describeDevice(
      "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 " +
      "(KHTML, like Gecko) FxiOS/121.0 Mobile/15E148 Safari/605.1.15"))
      .toBe("Firefox on iOS");
  });

  it("says it does not know rather than guessing", () => {
    // An absent User-Agent is normal — a script, a curl, a privacy extension —
    // and the session is still real and still worth listing. "Unknown device"
    // next to a sign-in time is honest; a confident wrong label is not.
    expect(describeDevice("")).toBe("Unknown device");
    expect(describeDevice("   ")).toBe("Unknown device");
    expect(describeDevice("curl/8.4.0")).toBe("Unknown device");
  });

  it("gives half an answer when it only has half", () => {
    expect(describeDevice("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")).toBe("Windows");
    expect(describeDevice("Chrome/120.0.0.0")).toBe("Chrome");
  });
});
