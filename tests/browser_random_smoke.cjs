"use strict";
// Optional developer QA; Playwright is not a scheduler runtime dependency.
const {chromium} = require('playwright');
const path = require('path');
const {pathToFileURL} = require('url');

(async () => {
    if (process.argv.length < 4) throw new Error('Usage: node tests/browser_random_smoke.cjs CASE_HTML INDEX_HTML');
    const file=path.resolve(process.argv[2]);
    const index=path.resolve(process.argv[3]);
    const browser=await chromium.launch({headless:true,
        executablePath:process.env.TTCAN_BROWSER_EXECUTABLE||undefined,
        args:process.env.TTCAN_BROWSER_EXECUTABLE?
            ['--no-sandbox','--no-zygote','--disable-dev-shm-usage','--single-process']:[]});
    const page=await browser.newPage({viewport:{width:1440,height:1100}});
    const errors=[], network=[];
    page.on('pageerror',error=>errors.push(error.message));
    page.on('request',request=>{if(/^https?:/.test(request.url()))network.push(request.url())});
    await page.goto(pathToFileURL(file).href);
    await page.waitForSelector('#matrix [data-cycle]');
    const data=JSON.parse(await page.locator('#schedule-data').textContent());
    const gen=JSON.parse(await page.locator('#generation-data').textContent());
    const result={node_count:data.config.nodes.length, matrix_cycles:data.config.network.matrix_cycles,
        initial_rows:await page.locator('#matrix [data-cycle]').count(),
        random_metadata_visible:await page.locator('#random-case-summary').innerText()};
    if(process.env.TTCAN_SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.TTCAN_SCREENSHOT_DIR,'ttcan_random_desktop.png')});
    await page.click('#all');
    result.full_rows=await page.locator('#matrix [data-cycle]').count();
    const node=data.config.nodes.find(n=>(data.message_maps[n.node_id]||[]).length>0);
    if(!node)throw new Error('The browser smoke case must contain at least one message map');
    await page.selectOption('#node',node.node_id);
    result.filtered_nodes=await page.locator('#triggers .node-title').allTextContents();
    result.trigger_rows=await page.locator('#triggers tbody tr').count();
    result.expected_trigger_rows=data.triggers[node.node_id].length;
    const mapping=data.message_maps[node.node_id][0];
    await page.selectOption('#message',mapping.message_name);
    await page.locator('#maps button').first().click();
    result.inspector=await page.locator('#inspector').innerText();
    result.highlighted_rows=await page.locator('tr.highlight').count();
    await page.click('#reset');
    const merged=data.windows.find(w=>w.kind==='merged');
    if(merged){
        await page.fill('#start',String(merged.cycle));
        await page.fill('#count','1');
        await page.selectOption('#kind','merged');
        result.merged_window_visible=(await page.locator('#matrix .block.merged').count())>0;
        await page.locator('#matrix .block.merged').first().click();
        const inspection=await page.locator('#inspector').innerText();
        const active=Object.values(data.triggers).flat().filter(t=>t.group_id===merged.group_id&&t.active_cycles.includes(merged.cycle));
        result.merged_trigger_mapping=inspection.includes(merged.group_id)&&active.length>0&&
            active.every(t=>merged.messages.includes(t.message_name));
    }else {result.merged_window_visible=null;result.merged_trigger_mapping=null;}
    await page.click('#reset');
    await page.setViewportSize({width:390,height:844});
    await page.evaluate(()=>window.scrollTo(0,0));
    result.mobile_overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth);
    if(process.env.TTCAN_SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.TTCAN_SCREENSHOT_DIR,'ttcan_random_mobile.png')});
    await page.goto(pathToFileURL(index).href);
    const link=page.locator('a[href$="/schedule.html"]').first();
    result.index_schedule_links=await page.locator('a[href$="/schedule.html"]').count();
    await link.click();
    await page.waitForSelector('#matrix [data-cycle]');
    result.index_link_opens=await page.title()==='TTCAN Schedule Studio';
    result.page_errors=errors;
    result.external_requests=network;
    result.browser_version=browser.version();
    result.passed=result.initial_rows===Math.min(5,result.matrix_cycles)&&result.full_rows===result.matrix_cycles&&
        result.filtered_nodes.length===1&&result.filtered_nodes[0].includes(node.node_name)&&
        result.trigger_rows===result.expected_trigger_rows&&result.inspector.includes(mapping.message_name)&&
        result.highlighted_rows>0&&(result.merged_window_visible!==false)&&(result.merged_trigger_mapping!==false)&&!result.mobile_overflow&&
        result.index_schedule_links>0&&result.index_link_opens&&errors.length===0&&network.length===0&&
        result.random_metadata_visible.includes(gen.master_seed)&&result.random_metadata_visible.includes(gen.case_seed);
    await browser.close();
    console.log(JSON.stringify(result,null,2));
    process.exitCode=result.passed?0:1;
})().catch(error=>{console.error(error);process.exitCode=1});
