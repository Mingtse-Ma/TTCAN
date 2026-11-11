"use strict";
// Optional developer QA. Requires Playwright; not a runtime dependency.
const {chromium} = require('playwright');
const path = require('path');
const {pathToFileURL} = require('url');

(async () => {
    const target = path.resolve(process.argv[2] || path.join(__dirname, '../output/schedule.html'));
    const browser = await chromium.launch({headless: true,
        executablePath: process.env.TTCAN_BROWSER_EXECUTABLE || undefined,
        args: process.env.TTCAN_BROWSER_EXECUTABLE ?
            ['--no-sandbox', '--no-zygote', '--disable-dev-shm-usage', '--single-process'] : []});
    const page = await browser.newPage({viewport: {width: 1440, height: 1100}});
    const errors = [], network = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
        if (/^https?:/.test(request.url())) network.push(request.url());
    });
    await page.goto(pathToFileURL(target).href);
    await page.waitForSelector('#matrix [data-cycle]');
    const result = {initial_rows: await page.locator('#matrix [data-cycle]').count()};
    if (process.env.TTCAN_SCREENSHOT_DIR)
        await page.screenshot({path:path.join(process.env.TTCAN_SCREENSHOT_DIR,'ttcan_desktop.png')});
    await page.click('#all');
    result.full_matrix_rows = await page.locator('#matrix [data-cycle]').count();
    await page.selectOption('#node', 'NodeB');
    result.filtered_node_tables = await page.locator('#triggers .node-title').allTextContents();
    await page.selectOption('#message', 'Msg7');
    await page.locator('#maps button', {hasText: 'Msg7'}).first().click();
    const inspector = await page.locator('#inspector').innerText();
    result.mapping_link = inspector.includes('rx_std_filter 0') && inspector.includes('tx_buffer 7');
    result.highlighted_rows = await page.locator('tr.highlight').count();
    await page.selectOption('#kind', 'exclusive');
    await page.fill('#start', '3');
    await page.fill('#count', '2');
    result.custom_range_rows = await page.locator('#matrix [data-cycle]').count();
    await page.fill('#zoom', '2');
    result.zoomed = await page.locator('#matrix').evaluate(el=>parseFloat(el.style.width)>1500);
    await page.click('#reset');
    result.reset_rows = await page.locator('#matrix [data-cycle]').count();
    await page.setViewportSize({width:390,height:844});
    await page.evaluate(()=>window.scrollTo(0,0));
    result.mobile_page_overflow = await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth);
    if (process.env.TTCAN_SCREENSHOT_DIR)
        await page.screenshot({path:path.join(process.env.TTCAN_SCREENSHOT_DIR,'ttcan_mobile.png')});
    result.browser_version = browser.version();
    result.page_errors = errors;
    result.external_requests = network;
    result.passed = result.initial_rows===5 && result.full_matrix_rows===64 &&
        result.filtered_node_tables.length===1 && result.filtered_node_tables[0].includes('NodeB') &&
        result.mapping_link && result.highlighted_rows>=3 && result.custom_range_rows===2 &&
        result.zoomed && result.reset_rows===5 && !result.mobile_page_overflow &&
        !errors.length && !network.length;
    await browser.close();
    console.log(JSON.stringify(result,null,2));
    process.exitCode = result.passed ? 0 : 1;
})().catch(error=>{console.error(error);process.exitCode=1;});
