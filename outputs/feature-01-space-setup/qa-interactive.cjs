const { chromium } = require("playwright");
const path = require("path");

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on("console", (message) => { if (message.type() === "error") errors.push(`console: ${message.text()}`); });
  page.on("pageerror", (error) => errors.push(`page: ${error.message}`));

  await page.goto("http://127.0.0.1:8766/?qa=cover#1", { waitUntil: "networkidle" });
  await page.waitForTimeout(900);
  if (!(await page.locator("h1").innerText()).includes("Understand every physical space")) throw new Error("Platform cover positioning is missing");
  await page.addStyleTag({ content: ".slide.active{animation:none!important}" });
  await page.screenshot({ path: path.resolve("outputs/feature-01-space-setup/cover-platform.png") });

  await page.goto("http://127.0.0.1:8766/#3", { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "3D insights" }).click();
  await page.waitForTimeout(1200);
  await page.getByRole("button", { name: "Buys", exact: true }).click();
  if (!(await page.getByRole("button", { name: "Buys", exact: true }).getAttribute("class") || "").includes("active")) throw new Error("Shelf metric did not switch to buys");

  const canvas = page.locator("#analysis3dViewport canvas");
  const box = await canvas.boundingBox();
  let hovered = false;
  for (let y = .28; y <= .72 && !hovered; y += .08) {
    for (let x = .22; x <= .76 && !hovered; x += .06) {
      await page.mouse.move(box.x + box.width * x, box.y + box.height * y);
      hovered = (await page.locator("#shelfMetricTooltip").getAttribute("class") || "").includes("visible");
    }
  }
  if (!hovered) throw new Error("No shelf cell exposed hover details");
  const hoverText = await page.locator("#shelfMetricTooltip").innerText();
  if (!hoverText.includes("Impressions") || !hoverText.includes("Avg. attention") || !hoverText.includes("Purchases")) throw new Error("Hover card does not expose all shelf values");
  await page.screenshot({ path: path.resolve("outputs/feature-01-space-setup/analysis-3d.png"), fullPage: true });

  await page.getByRole("button", { name: "Heatmap", exact: true }).click();
  if ((await page.getByRole("button", { name: "Heatmap", exact: true }).getAttribute("class") || "").includes("active")) throw new Error("Heatmap layer did not toggle");

  await page.goto("http://127.0.0.1:8766/?qa=live#4", { waitUntil: "networkidle" });
  await page.waitForTimeout(1200);
  await page.screenshot({ path: path.resolve("outputs/feature-01-space-setup/live-store.png"), fullPage: true });
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  if ((await page.locator("#pauseLive").innerText()).trim() !== "Resume") throw new Error("Live animation pause state did not change");

  await page.goto("http://127.0.0.1:8766/?qa=annotation#5", { waitUntil: "networkidle" });
  await page.waitForTimeout(800);
  await page.screenshot({ path: path.resolve("outputs/feature-01-space-setup/annotation.png"), fullPage: true });
  await page.getByRole("button", { name: "CAM-05", exact: true }).click();
  if (!(await page.getByRole("button", { name: "Wet floor", exact: true }).getAttribute("class") || "").includes("active")) throw new Error("Camera change did not recommend the matching label");
  await page.locator("#saveFrame").click();
  if ((await page.locator("#datasetBadge").innerText()).trim() !== "25") throw new Error("Saving a frame did not update the dataset count");
  await page.getByRole("button", { name: /Training data/ }).click();
  await page.getByRole("button", { name: "wet_floor", exact: true }).click();
  await page.screenshot({ path: path.resolve("outputs/feature-01-space-setup/annotation-dataset.png"), fullPage: true });

  await page.goto("http://127.0.0.1:8766/?qa=agent#6", { waitUntil: "networkidle" });
  await page.waitForTimeout(1400);
  await page.screenshot({ path: path.resolve("outputs/feature-01-space-setup/agent-interface.png"), fullPage: true });
  await page.getByRole("button", { name: "Run a model", exact: true }).click();
  if (!(await page.locator("#agentAnswerTitle").innerText()).includes("Queue model launched")) throw new Error("Custom-model agent workflow did not render");
  await page.getByRole("button", { name: "Train from data", exact: true }).click();
  if (!(await page.locator("#agentAnswerTitle").innerText()).includes("Fine-tuning prepared")) throw new Error("Fine-tuning agent workflow did not render");

  await page.goto("http://127.0.0.1:8766/?qa=accessibility#7", { waitUntil: "networkidle" });
  await page.waitForTimeout(1200);
  await page.screenshot({ path: path.resolve("outputs/feature-01-space-setup/accessibility-audit.png"), fullPage: true });
  await page.getByRole("button", { name: "Accessible proposal", exact: true }).click();
  if ((await page.locator("#accessScore").innerText()).trim() !== "94 / 100") throw new Error("Accessibility proposal did not update the score");
  if (!(await page.locator("#accessOutcome").innerText()).includes("1.22 m")) throw new Error("Accessibility proposal outcome is missing");
  await page.screenshot({ path: path.resolve("outputs/feature-01-space-setup/accessibility-proposal.png"), fullPage: true });

  await page.goto("http://127.0.0.1:8766/?qa=domains#8", { waitUntil: "networkidle" });
  await page.waitForTimeout(700);
  await page.screenshot({ path: path.resolve("outputs/feature-01-space-setup/domains-8.png"), fullPage: true });
  await page.locator('[data-domain="fashion"]').click();
  if (!(await page.locator('[data-domain="fashion"]').getAttribute("class") || "").includes("active")) throw new Error("Domain focus did not switch");

  if (errors.length) throw new Error(errors.join("\n"));
  console.log(JSON.stringify({ coverPositioning: true, analysisHover: hovered, pauseState: "Resume", annotationSaved: true, agentWorkflows: 3, accessibilityProposal: true, domainSwitch: true, errors: 0 }, null, 2));
  await browser.close();
})().catch((error) => { console.error(error); process.exit(1); });
