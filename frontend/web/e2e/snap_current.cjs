/* Screenshot helper: captures pages of the running dev server (light + dark). */
const { chromium } = require('playwright-core');
const path = require('path');

const BASE = process.env.BASE_URL || 'http://localhost:5199';
const OUT = path.resolve(__dirname, '..', 'tmp', 'shots');

const targets = [
  { name: 'home', url: '/' },
  { name: 'login', url: '/login' },
  { name: 'settings', url: '/settings' },
  { name: 'tasks', url: '/tasks' },
  { name: 'watchlist', url: '/watchlist' },
];

const LOGIN = { email: 'uibeauty@test.local', password: 'Test1234!ab' };

async function login(ctx) {
  const res = await ctx.request.post('http://localhost:8000/api/v1/account/login', {
    data: LOGIN,
  });
  if (!res.ok()) {
    console.log('login failed:', res.status(), await res.text());
  }
}

(async () => {
  const fs = require('fs');
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({
    executablePath:
      process.env.CHROMIUM_PATH ||
      'C:\\Users\\ZZC26\\AppData\\Local\\ms-playwright\\chromium-1223\\chrome-win64\\chrome.exe',
  });
  try {
    for (const theme of ['light', 'dark']) {
      const ctx = await browser.newContext({
        viewport: { width: 1440, height: 900 },
        colorScheme: theme,
      });
      await login(ctx);
      const page = await ctx.newPage();
      for (const t of targets) {
        try {
          await page.goto(BASE + t.url, { waitUntil: 'load', timeout: 20000 });
          await page.waitForTimeout(2500);
          await page.screenshot({
            path: path.join(OUT, `${t.name}_${theme}.png`),
            fullPage: false,
          });
          console.log(`captured ${t.name} (${theme})`);
        } catch (e) {
          console.log(`skip ${t.name} (${theme}): ${e.message.split('\n')[0]}`);
        }
      }
      // mobile viewport of first reachable target
      await ctx.close();
    }
    // mobile
    const mctx = await browser.newContext({
      viewport: { width: 390, height: 844 },
      colorScheme: 'light',
      isMobile: true,
      hasTouch: true,
    });
    await login(mctx);
    const mpage = await mctx.newPage();
    for (const t of targets) {
      try {
        await mpage.goto(BASE + t.url, { waitUntil: 'load', timeout: 20000 });
        await mpage.waitForTimeout(2000);
        await mpage.screenshot({ path: path.join(OUT, `m_${t.name}.png`) });
        console.log(`captured m_${t.name}`);
      } catch (e) {
        console.log(`skip m_${t.name}: ${e.message.split('\n')[0]}`);
      }
    }
    await mctx.close();
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
