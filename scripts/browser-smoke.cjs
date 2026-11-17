// Optional browser check: NODE_PATH=/tmp/cloudpulse-browser/node_modules node scripts/browser-smoke.cjs
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const path = require("node:path");

(async () => {
  const browser = await chromium.launch({
    executablePath:
      process.env.CHROME_PATH ||
      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    headless: true,
  });
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1120 },
      deviceScaleFactor: 1,
    });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("http://127.0.0.1:8080");
    await page
      .getByText("All services are operational", { exact: true })
      .waitFor();
    assert.equal(await page.locator(".service").count(), 3);
    assert.equal(await page.locator(".badge.up").count(), 3);
    assert.ok(await page.locator("#chart polyline").count());
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > window.innerWidth,
      ),
      false,
    );
    await page.screenshot({
      path: path.resolve("docs/dashboard-desktop.png"),
      fullPage: true,
    });
    await page
      .getByRole("button", { name: "Fail Billing", exact: true })
      .click();
    for (let i = 0; i < 2; i++) {
      await page.waitForTimeout(1100);
      await page
        .getByRole("button", { name: "Run check", exact: true })
        .click();
      await page.getByText("Check complete.", { exact: false }).waitFor();
      await page.waitForFunction(
        () => !document.getElementById("check").disabled,
      );
    }
    await page.locator(".badge.down").waitFor();
    await page.screenshot({
      path: path.resolve("docs/dashboard-incident.png"),
      fullPage: true,
    });
    await page
      .getByRole("button", { name: "Restore Billing", exact: true })
      .click();
    await page.waitForTimeout(1100);
    await page.getByRole("button", { name: "Run check", exact: true }).click();
    await page
      .getByText("All services are operational", { exact: true })
      .waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > window.innerWidth,
      ),
      false,
    );
    assert.equal(await page.locator(".service").count(), 3);
    await page.screenshot({
      path: path.resolve("docs/dashboard-mobile.png"),
      fullPage: true,
    });
    assert.deepEqual(errors, []);
    console.log(
      "PASS: desktop + mobile, no overflow or JS errors, failure + recovery, 3 real screenshots",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
